from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .metrics import gold_rank, is_gold_source, is_rejected
from .schemas import (
    CaseResult,
    CaseTransition,
    ComparisonReport,
    MetricDelta,
    RunReport,
    ThresholdPoint,
    ThresholdScanReport,
)


class AnalysisError(ValueError):
    """Raised when saved reports cannot support an analysis."""


def load_run_report(path: Path) -> RunReport:
    if not path.is_file():
        raise AnalysisError(f"评估报告不存在：{path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return RunReport.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise AnalysisError(f"评估报告格式错误：{path}：{exc}") from exc


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def _max_rerank_score(result: CaseResult) -> float:
    observation = result.retrieval
    if observation is None or not observation.results:
        return float("-inf")
    return max(
        source.rerank_score
        for source in observation.results
        if source.rerank_score is not None
    )


def _point_penalty(
    point: ThresholdPoint,
    *,
    max_false_accept_rate: float,
    max_false_reject_rate: float,
    min_gold_survival_rate: float,
) -> float:
    false_accept = point.false_accept_rate
    false_reject = point.false_reject_rate
    gold_survival = point.gold_survival_rate
    if (
        false_accept is None
        or false_reject is None
        or gold_survival is None
    ):
        return float("inf")
    return (
        max(0.0, false_accept - max_false_accept_rate)
        + max(0.0, false_reject - max_false_reject_rate)
        + max(0.0, min_gold_survival_rate - gold_survival)
    )


def scan_thresholds(
    report: RunReport,
    *,
    source_report: str,
    thresholds: list[float],
    max_false_accept_rate: float,
    max_false_reject_rate: float,
    min_gold_survival_rate: float,
) -> ThresholdScanReport:
    if not thresholds:
        raise AnalysisError("至少需要一个 threshold")
    raw_thresholds = [float(value) for value in thresholds]
    if any(
        not math.isfinite(value) or value < 0 or value > 1
        for value in raw_thresholds
    ):
        raise AnalysisError("threshold 必须是 0 到 1 的有限数值")
    normalized_thresholds = sorted(
        {round(value, 6) for value in raw_thresholds}
    )

    usable: list[CaseResult] = []
    api_errors = 0
    missing_scores = 0
    retrieval_present = 0
    for result in report.results:
        observation = result.retrieval
        if observation is None:
            continue
        retrieval_present += 1
        if observation.status == "error":
            api_errors += 1
            continue
        if any(
            source.rerank_score is None
            for source in observation.results
        ):
            missing_scores += 1
            continue
        usable.append(result)

    if retrieval_present != len(report.results):
        raise AnalysisError(
            "阈值扫描要求每个样本都有 retrieval 结果；"
            "请使用 retrieve 或 all 模式重新运行"
        )
    if missing_scores:
        raise AnalysisError(
            f"有 {missing_scores} 个检索样本缺少 rerank_score；"
            "请确认整次运行均启用 reranker shadow mode"
        )
    if not usable:
        raise AnalysisError(
            "报告中没有可用于阈值扫描的 rerank_score；"
            "请使用 RAG_RERANK_SHADOW_MODE=true 和 retrieve/all 模式"
        )

    answerable = [
        item
        for item in usable
        if item.case.expected_outcome == "answer"
    ]
    unanswerable = [
        item
        for item in usable
        if item.case.expected_outcome == "reject"
    ]
    if not answerable or not unanswerable:
        raise AnalysisError(
            "阈值扫描同时需要 answer 和 reject 两类可用样本"
        )

    points: list[ThresholdPoint] = []
    for threshold in normalized_thresholds:
        false_rejects = sum(
            _max_rerank_score(item) < threshold
            for item in answerable
        )
        false_accepts = sum(
            _max_rerank_score(item) >= threshold
            for item in unanswerable
        )
        gold_survivors = sum(
            any(
                is_gold_source(source, item.case)
                and source.rerank_score is not None
                and source.rerank_score >= threshold
                for source in item.retrieval.results
            )
            for item in answerable
            if item.retrieval is not None
        )
        accepted = sum(
            _max_rerank_score(item) >= threshold
            for item in usable
        )
        false_reject_rate = _ratio(false_rejects, len(answerable))
        false_accept_rate = _ratio(false_accepts, len(unanswerable))
        gold_survival_rate = _ratio(
            gold_survivors,
            len(answerable),
        )
        constraints_met = (
            false_reject_rate is not None
            and false_accept_rate is not None
            and gold_survival_rate is not None
            and false_reject_rate <= max_false_reject_rate
            and false_accept_rate <= max_false_accept_rate
            and gold_survival_rate >= min_gold_survival_rate
        )
        points.append(
            ThresholdPoint(
                threshold=threshold,
                answerable_count=len(answerable),
                unanswerable_count=len(unanswerable),
                false_reject_count=false_rejects,
                false_reject_rate=false_reject_rate,
                false_accept_count=false_accepts,
                false_accept_rate=false_accept_rate,
                gold_survival_count=gold_survivors,
                gold_survival_rate=gold_survival_rate,
                accepted_query_rate=_ratio(accepted, len(usable)),
                constraints_met=constraints_met,
            )
        )

    feasible = [point for point in points if point.constraints_met]
    if api_errors:
        recommendation = None
        constraints_met = False
        reason = (
            f"有 {api_errors} 个检索 API 错误，扫描曲线仅供排查；"
            "修复错误并完整重跑前不推荐阈值。"
        )
    elif feasible:
        recommendation = max(
            feasible,
            key=lambda point: point.threshold,
        )
        constraints_met = True
        reason = (
            "选择满足全部约束的最高阈值，优先减少无答案问题误放。"
        )
    else:
        recommendation = min(
            points,
            key=lambda point: (
                _point_penalty(
                    point,
                    max_false_accept_rate=max_false_accept_rate,
                    max_false_reject_rate=max_false_reject_rate,
                    min_gold_survival_rate=min_gold_survival_rate,
                ),
                point.false_accept_rate
                if point.false_accept_rate is not None
                else 1.0,
                -(
                    point.gold_survival_rate
                    if point.gold_survival_rate is not None
                    else 0.0
                ),
                point.threshold,
            ),
        )
        constraints_met = False
        reason = (
            "没有阈值满足全部约束，返回总约束违约量最小的候选；"
            "该值只能用于继续分析，不能视为已通过验收。"
        )

    return ThresholdScanReport(
        source_report=source_report,
        generated_at=datetime.now(UTC).isoformat(),
        constraints={
            "max_false_accept_rate": max_false_accept_rate,
            "max_false_reject_rate": max_false_reject_rate,
            "min_gold_survival_rate": min_gold_survival_rate,
        },
        coverage={
            "source_cases": len(report.results),
            "retrieval_present": retrieval_present,
            "usable_cases": len(usable),
            "answerable_cases": len(answerable),
            "unanswerable_cases": len(unanswerable),
            "api_errors": api_errors,
            "missing_score_cases": missing_scores,
        },
        recommended_threshold=(
            recommendation.threshold
            if recommendation is not None
            else None
        ),
        recommendation_constraints_met=constraints_met,
        recommendation_reason=reason,
        points=points,
    )


def _nested(summary: dict[str, Any], *path: str) -> float | int | None:
    value: Any = summary
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _metric_delta(
    *,
    metric: str,
    baseline: float | int | None,
    experiment: float | int | None,
    direction: str,
) -> MetricDelta:
    if baseline is None or experiment is None:
        delta = None
        improved = None
    else:
        delta = round(experiment - baseline, 4)
        if delta == 0:
            improved = None
        elif direction == "higher":
            improved = delta > 0
        else:
            improved = delta < 0
    return MetricDelta(
        metric=metric,
        baseline=baseline,
        experiment=experiment,
        delta=delta,
        direction=direction,
        improved=improved,
    )


def _gate_correct(result: CaseResult) -> bool | None:
    chat = result.chat
    if chat is None or chat.status != "ok":
        return None
    expected_reject = result.case.expected_outcome == "reject"
    return is_rejected(chat.finish_reason) == expected_reject


def _retrieval_correct(
    result: CaseResult,
    *,
    top_k: int,
) -> bool | None:
    observation = result.retrieval
    if (
        result.case.expected_outcome != "answer"
        or observation is None
        or observation.status != "ok"
    ):
        return None
    return (
        gold_rank(
            observation.results,
            result.case,
            top_k=top_k,
        )
        is not None
    )


def _citation_correct(result: CaseResult) -> bool | None:
    chat = result.chat
    if (
        result.case.expected_outcome != "answer"
        or chat is None
        or chat.status != "ok"
        or is_rejected(chat.finish_reason)
    ):
        return None
    return any(
        is_gold_source(source, result.case)
        for source in chat.citations
    )


def _gate_state(result: CaseResult) -> str:
    chat = result.chat
    if chat is None:
        return "not_evaluated"
    if chat.status == "error":
        return "api_error"
    return chat.finish_reason or "unknown"


def _retrieval_state(result: CaseResult, *, top_k: int) -> str:
    correct = _retrieval_correct(result, top_k=top_k)
    if correct is None:
        return "not_evaluated"
    return f"gold_{'hit' if correct else 'miss'}@{top_k}"


def _citation_state(result: CaseResult) -> str:
    correct = _citation_correct(result)
    if correct is None:
        return "not_evaluated"
    return "gold_hit" if correct else "gold_miss"


def _transition(
    *,
    case_id: str,
    category: str,
    kind: str,
    baseline: bool | None,
    experiment: bool | None,
    baseline_state: str,
    experiment_state: str,
) -> CaseTransition | None:
    if baseline is None or experiment is None or baseline == experiment:
        return None
    suffix = "improvement" if experiment else "regression"
    return CaseTransition(
        case_id=case_id,
        category=category,
        change=f"{kind}_{suffix}",
        baseline=baseline_state,
        experiment=experiment_state,
    )


def compare_reports(
    baseline: RunReport,
    experiment: RunReport,
    *,
    baseline_report: str,
    experiment_report: str,
) -> ComparisonReport:
    if baseline.config.top_k != experiment.config.top_k:
        raise AnalysisError("对比报告的 top_k 必须一致")
    top_k = baseline.config.top_k
    baseline_by_id = {item.case.id: item for item in baseline.results}
    experiment_by_id = {
        item.case.id: item for item in experiment.results
    }
    if len(baseline_by_id) != len(baseline.results) or len(
        experiment_by_id
    ) != len(experiment.results):
        raise AnalysisError("报告中存在重复样本 ID")
    baseline_ids = set(baseline_by_id)
    experiment_ids = set(experiment_by_id)
    if baseline_ids != experiment_ids:
        raise AnalysisError(
            "对比要求两份报告包含相同样本 ID；"
            f"baseline_only={len(baseline_ids - experiment_ids)}, "
            f"experiment_only={len(experiment_ids - baseline_ids)}"
        )
    for case_id in sorted(baseline_ids):
        left = baseline_by_id[case_id].case
        right = experiment_by_id[case_id].case
        if (
            left.question != right.question
            or left.category != right.category
            or left.expected_outcome != right.expected_outcome
            or left.filters != right.filters
            or set(left.gold_document_ids) != set(right.gold_document_ids)
            or set(left.gold_source_urls) != set(right.gold_source_urls)
            or {
                tuple(sorted(group)) for group in left.required_facts
            }
            != {
                tuple(sorted(group)) for group in right.required_facts
            }
        ):
            raise AnalysisError(f"样本标签不一致：{case_id}")

    specs = [
        (
            f"recall_at_{top_k}",
            ("retrieval", f"recall_at_{top_k}"),
            "higher",
        ),
        (
            f"mrr_at_{top_k}",
            ("retrieval", f"mrr_at_{top_k}"),
            "higher",
        ),
        (
            "false_reject_rate",
            ("gates", "false_reject_rate"),
            "lower",
        ),
        (
            "false_accept_rate",
            ("gates", "false_accept_rate"),
            "lower",
        ),
        (
            "citation_gold_hit_rate",
            ("answers", "citation_gold_hit_rate"),
            "higher",
        ),
        (
            "required_fact_coverage",
            ("answers", "required_fact_coverage"),
            "higher",
        ),
        (
            "retrieve_latency_p95_ms",
            ("efficiency", "retrieve_latency_ms", "p95"),
            "lower",
        ),
        (
            "chat_latency_p95_ms",
            ("efficiency", "chat_latency_ms", "p95"),
            "lower",
        ),
        (
            "reported_total_tokens",
            ("efficiency", "reported_total_tokens"),
            "lower",
        ),
        (
            "api_errors",
            ("cases", "api_errors"),
            "lower",
        ),
    ]
    metric_deltas = [
        _metric_delta(
            metric=name,
            baseline=_nested(baseline.summary, *path),
            experiment=_nested(experiment.summary, *path),
            direction=direction,
        )
        for name, path, direction in specs
    ]

    transitions: list[CaseTransition] = []
    for case_id in sorted(baseline_ids):
        left = baseline_by_id[case_id]
        right = experiment_by_id[case_id]
        for (
            kind,
            left_value,
            right_value,
            left_state,
            right_state,
        ) in (
            (
                "gate",
                _gate_correct(left),
                _gate_correct(right),
                _gate_state(left),
                _gate_state(right),
            ),
            (
                "retrieval",
                _retrieval_correct(left, top_k=top_k),
                _retrieval_correct(right, top_k=top_k),
                _retrieval_state(left, top_k=top_k),
                _retrieval_state(right, top_k=top_k),
            ),
            (
                "citation",
                _citation_correct(left),
                _citation_correct(right),
                _citation_state(left),
                _citation_state(right),
            ),
        ):
            transition = _transition(
                case_id=case_id,
                category=left.case.category,
                kind=kind,
                baseline=left_value,
                experiment=right_value,
                baseline_state=left_state,
                experiment_state=right_state,
            )
            if transition is not None:
                transitions.append(transition)

    return ComparisonReport(
        generated_at=datetime.now(UTC).isoformat(),
        baseline_report=baseline_report,
        experiment_report=experiment_report,
        baseline_label=baseline.config.label,
        experiment_label=experiment.config.label,
        sample_coverage={
            "baseline_cases": len(baseline_ids),
            "experiment_cases": len(experiment_ids),
            "common_cases": len(baseline_ids),
            "top_k": top_k,
        },
        metrics=metric_deltas,
        regressions=[
            item
            for item in transitions
            if item.change.endswith("_regression")
        ],
        improvements=[
            item
            for item in transitions
            if item.change.endswith("_improvement")
        ],
    )
