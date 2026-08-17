from __future__ import annotations

import math
import unicodedata
from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from .schemas import AssistantCaseResult, CaseResult, EvalCase


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


ASSISTANT_EXPECTED_TOOLS = {
    "policy": "policy_knowledge",
    "device_price": "device_price",
}


def _policy_evidence_complete(item: Any) -> bool:
    return bool(
        item.type == "policy"
        and item.title.strip()
        and item.excerpt.strip()
        and item.chunk_id.strip()
        and item.document_id.strip()
        and item.source_url.startswith(("http://", "https://"))
    )


def _price_evidence_complete(item: Any) -> bool:
    try:
        Decimal(item.price)
    except (InvalidOperation, ValueError):
        return False
    return bool(
        item.type == "device_price"
        and item.title.strip()
        and item.currency.strip()
        and item.source.strip()
        and item.observed_at.strip()
        and (
            item.official_product_id.strip()
            or item.official_sku_id.strip()
        )
    )


def assistant_case_checks(result: AssistantCaseResult) -> dict[str, bool]:
    case = result.case
    chat = result.chat
    if chat.status != "ok":
        return {"passed": False}
    routing = (
        chat.mode == case.mode
        and chat.used_tool == ASSISTANT_EXPECTED_TOOLS[case.mode]
    )
    finish = chat.finish_reason in case.expected_finish_reasons
    reason = (
        not case.expected_reason_codes
        or chat.reason_code in case.expected_reason_codes
    )
    missing = set(case.expected_missing_fields).issubset(
        chat.missing_fields
    )
    expected_minimum = case.min_evidence_count or 0
    evidence_count = len(chat.evidence) >= expected_minimum
    if case.expected_outcome == "answer":
        evidence_shape = bool(chat.evidence) and all(
            item.type == case.mode for item in chat.evidence
        )
        evidence_complete = bool(chat.evidence) and all(
            _policy_evidence_complete(item)
            if case.mode == "policy"
            else _price_evidence_complete(item)
            for item in chat.evidence
        )
    else:
        evidence_shape = not chat.evidence
        evidence_complete = not chat.evidence
    expected_ids = set(case.expected_product_ids)
    expected_skus = set(case.expected_sku_ids)
    candidate_hit = (
        not expected_ids
        and not expected_skus
        or any(
            item.official_product_id in expected_ids
            or item.official_sku_id in expected_skus
            for item in chat.evidence
        )
    )
    checks = {
        "routing": routing,
        "finish": finish,
        "reason": reason,
        "missing": missing,
        "evidence_count": evidence_count,
        "evidence_shape": evidence_shape,
        "evidence_complete": evidence_complete,
        "candidate_hit": candidate_hit,
    }
    checks["passed"] = all(checks.values())
    return checks


def calculate_assistant_metrics(
    results: list[AssistantCaseResult],
) -> dict[str, Any]:
    successful = [item for item in results if item.chat.status == "ok"]
    checks = {
        item.case.id: assistant_case_checks(item) for item in results
    }
    routing_correct = sum(
        bool(checks[item.case.id].get("routing")) for item in successful
    )
    finish_correct = sum(
        bool(checks[item.case.id].get("finish")) for item in successful
    )
    reason_cases = [
        item for item in successful if item.case.expected_reason_codes
    ]
    missing_cases = [
        item for item in successful if item.case.expected_missing_fields
    ]
    answer_cases = [
        item
        for item in successful
        if item.case.expected_outcome == "answer"
    ]
    non_answer_cases = [
        item
        for item in successful
        if item.case.expected_outcome != "answer"
    ]
    price_gold_cases = [
        item
        for item in successful
        if item.case.expected_product_ids or item.case.expected_sku_ids
    ]
    all_evidence = [
        evidence for item in successful for evidence in item.chat.evidence
    ]
    policy_evidence = [
        item for item in all_evidence if item.type == "policy"
    ]
    price_evidence = [
        item for item in all_evidence if item.type == "device_price"
    ]
    passed = sum(bool(value.get("passed")) for value in checks.values())
    latencies = [item.chat.client_elapsed_ms for item in results]
    total_tokens = sum(
        int(item.chat.usage.get("total_tokens", 0) or 0)
        for item in successful
    )
    finish_distribution = Counter(
        item.chat.finish_reason or "missing" for item in successful
    )
    reason_distribution = Counter(
        item.chat.reason_code or "none" for item in successful
    )

    by_mode: dict[str, dict[str, Any]] = {}
    for mode in ("policy", "device_price"):
        mode_results = [item for item in results if item.case.mode == mode]
        mode_passed = sum(
            bool(checks[item.case.id].get("passed")) for item in mode_results
        )
        by_mode[mode] = {
            "cases": len(mode_results),
            "errors": sum(
                item.chat.status == "error" for item in mode_results
            ),
            "passed": mode_passed,
            "pass_rate": _ratio(mode_passed, len(mode_results)),
        }

    return {
        "cases": {
            "total": len(results),
            "successful": len(successful),
            "errors": len(results) - len(successful),
            "passed": passed,
            "pass_rate": _ratio(passed, len(results)),
        },
        "routing": {
            "evaluated": len(successful),
            "correct": routing_correct,
            "accuracy": _ratio(routing_correct, len(successful)),
        },
        "outcomes": {
            "evaluated": len(successful),
            "finish_reason_correct": finish_correct,
            "finish_reason_accuracy": _ratio(
                finish_correct, len(successful)
            ),
            "reason_code_evaluated": len(reason_cases),
            "reason_code_correct": sum(
                bool(checks[item.case.id].get("reason"))
                for item in reason_cases
            ),
            "missing_fields_evaluated": len(missing_cases),
            "missing_fields_correct": sum(
                bool(checks[item.case.id].get("missing"))
                for item in missing_cases
            ),
            "finish_reason_distribution": dict(
                sorted(finish_distribution.items())
            ),
            "reason_code_distribution": dict(
                sorted(reason_distribution.items())
            ),
        },
        "evidence": {
            "answer_cases": len(answer_cases),
            "minimum_count_satisfied": sum(
                bool(checks[item.case.id].get("evidence_count"))
                for item in answer_cases
            ),
            "correct_type_cases": sum(
                bool(checks[item.case.id].get("evidence_shape"))
                for item in answer_cases
            ),
            "complete_cases": sum(
                bool(checks[item.case.id].get("evidence_complete"))
                for item in answer_cases
            ),
            "non_answer_cases": len(non_answer_cases),
            "unsupported_evidence_leaks": sum(
                bool(item.chat.evidence) for item in non_answer_cases
            ),
            "policy_items": len(policy_evidence),
            "policy_items_complete": sum(
                _policy_evidence_complete(item) for item in policy_evidence
            ),
            "price_items": len(price_evidence),
            "price_items_complete": sum(
                _price_evidence_complete(item) for item in price_evidence
            ),
            "price_gold_cases": len(price_gold_cases),
            "price_candidate_hits": sum(
                bool(checks[item.case.id].get("candidate_hit"))
                for item in price_gold_cases
            ),
            "price_candidate_recall": _ratio(
                sum(
                    bool(checks[item.case.id].get("candidate_hit"))
                    for item in price_gold_cases
                ),
                len(price_gold_cases),
            ),
        },
        "efficiency": {
            "chat_latency_ms": {
                "p50": _percentile(latencies, 0.5),
                "p95": _percentile(latencies, 0.95),
            },
            "reported_total_tokens": total_tokens,
        },
        "by_mode": by_mode,
    }
