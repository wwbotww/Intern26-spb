from __future__ import annotations

import math
import unicodedata
from collections import Counter
from typing import Any, Protocol

from .schemas import CaseResult, EvalCase


REJECTION_REASONS = frozenset(
    {"no_context", "reranker_rejected", "llm_rejected"}
)


class SourceLike(Protocol):
    document_id: str
    source_url: str


def _ratio(numerator: int | float, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(float(numerator) / denominator, 4)


def _wilson_interval(
    numerator: int,
    denominator: int,
    *,
    z: float = 1.959963984540054,
) -> dict[str, float] | None:
    """Return a 95% Wilson score interval for a binomial proportion."""
    if denominator == 0:
        return None
    proportion = numerator / denominator
    z_squared = z * z
    denominator_term = 1 + z_squared / denominator
    center = (
        proportion + z_squared / (2 * denominator)
    ) / denominator_term
    margin = (
        z
        * math.sqrt(
            proportion * (1 - proportion) / denominator
            + z_squared / (4 * denominator * denominator)
        )
        / denominator_term
    )
    return {
        "lower": round(max(0.0, center - margin), 4),
        "upper": round(min(1.0, center + margin), 4),
    }


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 3)


def is_gold_source(source: SourceLike, case: EvalCase) -> bool:
    if source.document_id in case.gold_document_ids:
        return True
    normalized_url = source.source_url.rstrip("/")
    return normalized_url in {
        value.rstrip("/") for value in case.gold_source_urls
    }


def fact_coverage(answer: str, facts: list[list[str]]) -> float:
    if not facts:
        return 0.0

    def normalize(value: str) -> str:
        return "".join(
            character
            for character in unicodedata.normalize(
                "NFKC",
                value.lower(),
            )
            if not character.isspace()
            and not unicodedata.category(character).startswith(
                ("P", "S")
            )
        )

    normalized = normalize(answer)
    matched = sum(
        any(
            normalize(term) in normalized
            for term in alternatives
        )
        for alternatives in facts
    )
    return matched / len(facts)


def is_rejected(finish_reason: str) -> bool:
    return finish_reason in REJECTION_REASONS


def gold_rank(
    sources: list[SourceLike],
    case: EvalCase,
    *,
    top_k: int,
) -> int | None:
    return next(
        (
            rank
            for rank, source in enumerate(sources[:top_k], start=1)
            if is_gold_source(source, case)
        ),
        None,
    )


def calculate_metrics(
    results: list[CaseResult],
    *,
    top_k: int,
    include_slices: bool = True,
) -> dict[str, Any]:
    categories = Counter(item.case.category for item in results)
    api_errors = sum(
        (
            item.retrieval is not None
            and item.retrieval.status == "error"
        )
        or (item.chat is not None and item.chat.status == "error")
        for item in results
    )

    retrieval_cases = [
        item
        for item in results
        if item.case.expected_outcome == "answer"
        and item.retrieval is not None
    ]
    retrieval_hits = 0
    reciprocal_rank_sum = 0.0
    retrieval_errors = 0
    for item in retrieval_cases:
        observation = item.retrieval
        assert observation is not None
        if observation.status == "error":
            retrieval_errors += 1
            continue
        first_gold_rank = gold_rank(
            observation.results,
            item.case,
            top_k=top_k,
        )
        if first_gold_rank is not None:
            retrieval_hits += 1
            reciprocal_rank_sum += 1 / first_gold_rank

    successful_chats = [
        item
        for item in results
        if item.chat is not None and item.chat.status == "ok"
    ]
    answerable_chats = [
        item
        for item in successful_chats
        if item.case.expected_outcome == "answer"
    ]
    reject_chats = [
        item
        for item in successful_chats
        if item.case.expected_outcome == "reject"
    ]
    false_rejects = sum(
        item.chat is not None
        and is_rejected(item.chat.finish_reason)
        for item in answerable_chats
    )
    false_accepts = sum(
        item.chat is not None
        and not is_rejected(item.chat.finish_reason)
        for item in reject_chats
    )
    stage_distribution = Counter(
        item.chat.finish_reason
        for item in successful_chats
        if item.chat is not None
    )

    citation_cases = [
        item
        for item in answerable_chats
        if item.chat is not None
        and not is_rejected(item.chat.finish_reason)
    ]
    citation_hits = sum(
        any(
            is_gold_source(source, item.case)
            for source in item.chat.citations
        )
        for item in citation_cases
        if item.chat is not None
    )
    fact_cases = [
        item
        for item in answerable_chats
        if item.case.required_facts
    ]
    fact_coverage_sum = sum(
        fact_coverage(
            item.chat.answer
            if item.chat is not None
            and not is_rejected(item.chat.finish_reason)
            else "",
            item.case.required_facts,
        )
        for item in fact_cases
    )

    retrieval_latencies = [
        item.retrieval.client_elapsed_ms
        for item in results
        if item.retrieval is not None
        and item.retrieval.status == "ok"
    ]
    chat_latencies = [
        item.chat.client_elapsed_ms
        for item in successful_chats
        if item.chat is not None
    ]
    total_tokens = sum(
        int(item.chat.usage.get("total_tokens", 0))
        for item in successful_chats
        if item.chat is not None
        and isinstance(item.chat.usage.get("total_tokens", 0), int)
    )
    rejected_chats = sum(
        item.chat is not None
        and is_rejected(item.chat.finish_reason)
        for item in successful_chats
    )

    summary = {
        "cases": {
            "total": len(results),
            "categories": dict(sorted(categories.items())),
            "outcomes": dict(
                sorted(
                    Counter(
                        item.case.expected_outcome
                        for item in results
                    ).items()
                )
            ),
            "difficulties": dict(
                sorted(
                    Counter(
                        item.case.difficulty for item in results
                    ).items()
                )
            ),
            "source_types": dict(
                sorted(
                    Counter(
                        item.case.source_type or "unspecified"
                        for item in results
                    ).items()
                )
            ),
            "api_errors": api_errors,
        },
        "retrieval": {
            "evaluated": len(retrieval_cases),
            "errors": retrieval_errors,
            f"recall_at_{top_k}": _ratio(
                retrieval_hits,
                len(retrieval_cases),
            ),
            f"recall_at_{top_k}_ci95": _wilson_interval(
                retrieval_hits,
                len(retrieval_cases),
            ),
            f"mrr_at_{top_k}": (
                round(reciprocal_rank_sum / len(retrieval_cases), 4)
                if retrieval_cases
                else None
            ),
            f"gold_survival_at_{top_k}": _ratio(
                retrieval_hits,
                len(retrieval_cases),
            ),
        },
        "gates": {
            "answerable_evaluated": len(answerable_chats),
            "unanswerable_evaluated": len(reject_chats),
            "false_reject_count": false_rejects,
            "false_reject_rate": _ratio(
                false_rejects,
                len(answerable_chats),
            ),
            "false_reject_rate_ci95": _wilson_interval(
                false_rejects,
                len(answerable_chats),
            ),
            "false_accept_count": false_accepts,
            "false_accept_rate": _ratio(
                false_accepts,
                len(reject_chats),
            ),
            "false_accept_rate_ci95": _wilson_interval(
                false_accepts,
                len(reject_chats),
            ),
            "finish_reason_distribution": dict(
                sorted(stage_distribution.items())
            ),
        },
        "answers": {
            "citation_evaluated": len(citation_cases),
            "citation_gold_hits": citation_hits,
            "citation_gold_hit_rate": _ratio(
                citation_hits,
                len(citation_cases),
            ),
            "citation_gold_hit_rate_ci95": _wilson_interval(
                citation_hits,
                len(citation_cases),
            ),
            "fact_evaluated": len(fact_cases),
            "required_fact_coverage": (
                round(fact_coverage_sum / len(fact_cases), 4)
                if fact_cases
                else None
            ),
        },
        "efficiency": {
            "retrieve_latency_ms": {
                "p50": _percentile(retrieval_latencies, 0.5),
                "p95": _percentile(retrieval_latencies, 0.95),
            },
            "chat_latency_ms": {
                "p50": _percentile(chat_latencies, 0.5),
                "p95": _percentile(chat_latencies, 0.95),
            },
            "reported_total_tokens": total_tokens,
            "rejection_rate": _ratio(
                rejected_chats,
                len(successful_chats),
            ),
        },
    }
    if include_slices:
        dimensions: dict[str, dict[str, list[CaseResult]]] = {
            "category": {},
            "difficulty": {},
            "source_type": {},
            "split": {},
        }
        for result in results:
            values = {
                "category": result.case.category,
                "difficulty": result.case.difficulty,
                "source_type": result.case.source_type or "unspecified",
                "split": result.case.split,
            }
            for dimension, value in values.items():
                dimensions[dimension].setdefault(value, []).append(result)
        summary["slices"] = {
            dimension: {
                value: calculate_metrics(
                    subset,
                    top_k=top_k,
                    include_slices=False,
                )
                for value, subset in sorted(groups.items())
            }
            for dimension, groups in dimensions.items()
        }
    return summary
