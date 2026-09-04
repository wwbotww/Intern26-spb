from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .metrics import (
    agent_turn_checks,
    calculate_agent_metrics,
    calculate_metrics,
    gold_rank,
    is_gold_source,
    is_rejected,
)
from .schemas import (
    AgentCaseResult,
    AgentComparisonReport,
    AgentRunReport,
    AgentTurnResult,
    AgentTurnTransition,
    CaseResult,
    CaseTransition,
    ComparisonReport,
    EvalCase,
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


def load_agent_run_report(path: Path) -> AgentRunReport:
    if not path.is_file():
        raise AnalysisError(f"Agent 评估报告不存在：{path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return AgentRunReport.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise AnalysisError(
            f"Agent 评估报告格式错误：{path}：{exc}"
        ) from exc


def recalculate_report(
    report: RunReport,
    *,
    cases: list[EvalCase],
    dataset: str,
    label: str,
) -> RunReport:
    """Recalculate labels and metrics without calling the service again."""
    saved_by_id = {result.case.id: result for result in report.results}
    current_by_id = {case.id: case for case in cases}
    if len(saved_by_id) != len(report.results):
        raise AnalysisError("保存报告中存在重复样本 ID")
    if len(current_by_id) != len(cases):
        raise AnalysisError("数据集中存在重复样本 ID")
    if set(saved_by_id) != set(current_by_id):
        raise AnalysisError(
            "离线重算要求报告和数据集包含相同样本 ID；"
            f"report_only={len(set(saved_by_id) - set(current_by_id))}, "
            f"dataset_only={len(set(current_by_id) - set(saved_by_id))}"
        )

    rebuilt: list[CaseResult] = []
    for case_id in sorted(saved_by_id):
        saved = saved_by_id[case_id]
        current = current_by_id[case_id]
        if (
            saved.case.question != current.question
            or saved.case.filters != current.filters
        ):
            raise AnalysisError(
                "样本问题文本或请求过滤条件已变化，"
                f"不能复用在线结果：{case_id}"
            )
        rebuilt.append(
            CaseResult(
                case=current,
                retrieval=saved.retrieval,
                chat=saved.chat,
            )
        )

    config = report.config.model_copy(
        update={"label": label, "dataset": dataset}
    )
    summary = calculate_metrics(
        rebuilt,
        top_k=config.top_k,
    )
    for name in (
        "wall_elapsed_ms",
        "throughput_requests_per_second",
    ):
        previous = report.summary.get("efficiency", {}).get(name)
        if previous is not None:
            summary["efficiency"][name] = previous
    return RunReport(
        generated_at=datetime.now(UTC).isoformat(),
        config=config,
        service=report.service,
        summary=summary,
        results=rebuilt,
    )


def analyze_stability(
    reports: list[RunReport],
    *,
    source_reports: list[str],
) -> dict[str, Any]:
    """Aggregate repeated runs of the same frozen sample set."""
    if len(reports) < 2:
        raise AnalysisError("稳定性分析至少需要两份报告")
    if len(reports) != len(source_reports):
        raise AnalysisError("报告数量与来源路径数量不一致")

    reference = reports[0]
    reference_by_id = {
        result.case.id: result for result in reference.results
    }
    if len(reference_by_id) != len(reference.results):
        raise AnalysisError("报告中存在重复样本 ID")
    ordered_ids = sorted(reference_by_id)
    indexed_reports = [reference_by_id]
    for report in reports[1:]:
        if report.config.mode != reference.config.mode:
            raise AnalysisError("稳定性报告的 mode 必须一致")
        if report.config.top_k != reference.config.top_k:
            raise AnalysisError("稳定性报告的 top_k 必须一致")
        indexed = {result.case.id: result for result in report.results}
        if len(indexed) != len(report.results):
            raise AnalysisError("报告中存在重复样本 ID")
        if set(indexed) != set(reference_by_id):
            raise AnalysisError("稳定性分析要求完全相同的样本 ID")
        for case_id in ordered_ids:
            left = reference_by_id[case_id].case
            right = indexed[case_id].case
            if (
                left.question != right.question
                or left.expected_outcome != right.expected_outcome
                or left.gold_document_ids != right.gold_document_ids
                or left.gold_source_urls != right.gold_source_urls
                or left.required_facts != right.required_facts
            ):
                raise AnalysisError(f"样本标签不一致：{case_id}")
        indexed_reports.append(indexed)

    metric_paths = {
        "api_errors": ("cases", "api_errors"),
        f"recall_at_{reference.config.top_k}": (
            "retrieval",
            f"recall_at_{reference.config.top_k}",
        ),
        f"mrr_at_{reference.config.top_k}": (
            "retrieval",
            f"mrr_at_{reference.config.top_k}",
        ),
        "false_reject_rate": ("gates", "false_reject_rate"),
        "false_accept_rate": ("gates", "false_accept_rate"),
        "citation_gold_hit_rate": (
            "answers",
            "citation_gold_hit_rate",
        ),
        "required_fact_coverage": (
            "answers",
            "required_fact_coverage",
        ),
        "chat_latency_p50_ms": (
            "efficiency",
            "chat_latency_ms",
            "p50",
        ),
        "chat_latency_p95_ms": (
            "efficiency",
            "chat_latency_ms",
            "p95",
        ),
        "retrieve_latency_p50_ms": (
            "efficiency",
            "retrieve_latency_ms",
            "p50",
        ),
        "retrieve_latency_p95_ms": (
            "efficiency",
            "retrieve_latency_ms",
            "p95",
        ),
        "reported_total_tokens": (
            "efficiency",
            "reported_total_tokens",
        ),
    }
    metric_series: dict[str, dict[str, Any]] = {}
    for name, path in metric_paths.items():
        values = [_nested(report.summary, *path) for report in reports]
        numeric = [
            float(value)
            for value in values
            if value is not None
        ]
        metric_series[name] = {
            "values": values,
            "min": round(min(numeric), 4) if numeric else None,
            "max": round(max(numeric), 4) if numeric else None,
            "range": (
                round(max(numeric) - min(numeric), 4)
                if numeric
                else None
            ),
        }

    changed: dict[str, list[str]] = {
        "finish_reason": [],
        "citation_set": [],
        "exact_answer": [],
    }
    for case_id in ordered_ids:
        observations = [
            indexed[case_id].chat for indexed in indexed_reports
        ]
        finish_states = {
            (
                observation.status,
                observation.finish_reason,
            )
            if observation is not None
            else ("missing", "")
            for observation in observations
        }
        citation_sets = {
            tuple(
                sorted(
                    {
                        citation.document_id
                        for citation in observation.citations
                    }
                )
            )
            if observation is not None
            else ()
            for observation in observations
        }
        answer_texts = {
            observation.answer if observation is not None else ""
            for observation in observations
        }
        if len(finish_states) > 1:
            changed["finish_reason"].append(case_id)
        if len(citation_sets) > 1:
            changed["citation_set"].append(case_id)
        if len(answer_texts) > 1:
            changed["exact_answer"].append(case_id)

    total = len(ordered_ids)
    consistency = {
        key: {
            "stable_cases": total - len(case_ids),
            "changed_cases": len(case_ids),
            "rate": _ratio(total - len(case_ids), total),
            "case_ids": case_ids,
        }
        for key, case_ids in changed.items()
    }
    quality_metrics = (
        f"recall_at_{reference.config.top_k}",
        f"mrr_at_{reference.config.top_k}",
        "false_reject_rate",
        "false_accept_rate",
        "citation_gold_hit_rate",
        "required_fact_coverage",
    )
    available_quality_metrics = [
        name
        for name in quality_metrics
        if metric_series[name]["range"] is not None
    ]
    quality_stable = (
        bool(available_quality_metrics)
        and all(
            metric_series[name]["range"] == 0
            for name in available_quality_metrics
        )
        and all(
            value == 0
            for value in metric_series["api_errors"]["values"]
        )
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "source_reports": source_reports,
        "labels": [report.config.label for report in reports],
        "run_count": len(reports),
        "case_count": total,
        "mode": reference.config.mode,
        "top_k": reference.config.top_k,
        "quality_stable": quality_stable,
        "metrics": metric_series,
        "consistency": consistency,
    }


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


def compare_agent_reports(
    baseline: AgentRunReport,
    experiment: AgentRunReport,
    *,
    baseline_report: str,
    experiment_report: str,
) -> AgentComparisonReport:
    """Compare two V2 Agent runs over one frozen labeled dataset."""

    baseline_hash = baseline.config.dataset_sha256.strip()
    experiment_hash = experiment.config.dataset_sha256.strip()
    if not _is_sha256(baseline_hash) or not _is_sha256(experiment_hash):
        raise AnalysisError(
            "Agent 对比要求两份报告都包含合法 dataset_sha256"
        )
    if baseline_hash != experiment_hash:
        raise AnalysisError("Agent 对比报告的 dataset_sha256 必须一致")
    if baseline.config.thresholds != experiment.config.thresholds:
        raise AnalysisError("Agent 对比报告的质量门禁阈值必须一致")

    baseline_by_id = _agent_results_by_id(baseline)
    experiment_by_id = _agent_results_by_id(experiment)
    baseline_ids = set(baseline_by_id)
    experiment_ids = set(experiment_by_id)
    if baseline_ids != experiment_ids:
        raise AnalysisError(
            "Agent 对比要求两份报告包含相同样本 ID；"
            f"baseline_only={len(baseline_ids - experiment_ids)}, "
            f"experiment_only={len(experiment_ids - baseline_ids)}"
        )

    for case_id in sorted(baseline_ids):
        left = baseline_by_id[case_id].case.model_dump(mode="json")
        right = experiment_by_id[case_id].case.model_dump(mode="json")
        if left != right:
            raise AnalysisError(f"Agent 样本标签不一致：{case_id}")
        _agent_turns_by_index(baseline_by_id[case_id])
        _agent_turns_by_index(experiment_by_id[case_id])

    baseline_summary = calculate_agent_metrics(
        baseline.results,
        thresholds=baseline.config.thresholds,
    )
    experiment_summary = calculate_agent_metrics(
        experiment.results,
        thresholds=experiment.config.thresholds,
    )

    specs = [
        ("case_pass_rate", ("cases", "pass_rate"), "higher"),
        ("turn_pass_rate", ("turns", "pass_rate"), "higher"),
        (
            "intent_accuracy",
            ("understanding", "intent_accuracy"),
            "higher",
        ),
        (
            "required_input_accuracy",
            ("understanding", "required_input_accuracy"),
            "higher",
        ),
        (
            "unnecessary_clarification_rate",
            ("understanding", "unnecessary_clarification_rate"),
            "lower",
        ),
        (
            "wrong_tool_rate",
            ("routing", "wrong_tool_rate"),
            "lower",
        ),
        (
            "task_completion_rate",
            ("completion", "task_completion_rate"),
            "higher",
        ),
        (
            "recovery_rate",
            ("recovery", "recovery_rate"),
            "higher",
        ),
        ("api_error_rate", ("turns", "api_error_rate"), "lower"),
        (
            "turn_latency_p95_ms",
            ("efficiency", "turn_latency_ms", "p95"),
            "lower",
        ),
    ]
    metric_deltas = [
        _metric_delta(
            metric=name,
            baseline=_nested(baseline_summary, *path),
            experiment=_nested(experiment_summary, *path),
            direction=direction,
        )
        for name, path, direction in specs
    ]

    transitions: list[AgentTurnTransition] = []
    expected_turns = 0
    for case_id in sorted(baseline_ids):
        left_case = baseline_by_id[case_id]
        right_case = experiment_by_id[case_id]
        expected_turns += len(left_case.case.turns)
        left_turns = _agent_turns_by_index(left_case)
        right_turns = _agent_turns_by_index(right_case)
        for turn_index in range(1, len(left_case.case.turns) + 1):
            left_turn = left_turns.get(turn_index)
            right_turn = right_turns.get(turn_index)
            left_passed = _agent_turn_passed(left_turn)
            right_passed = _agent_turn_passed(right_turn)
            if left_passed == right_passed:
                continue
            transitions.append(
                AgentTurnTransition(
                    case_id=case_id,
                    category=left_case.case.category,
                    turn_index=turn_index,
                    change=(
                        "turn_improvement"
                        if right_passed
                        else "turn_regression"
                    ),
                    baseline=_agent_turn_state(left_turn),
                    experiment=_agent_turn_state(right_turn),
                )
            )

    baseline_gate = bool(baseline_summary["quality_gate"]["passed"])
    experiment_gate = bool(experiment_summary["quality_gate"]["passed"])
    return AgentComparisonReport(
        generated_at=datetime.now(UTC).isoformat(),
        baseline_report=baseline_report,
        experiment_report=experiment_report,
        baseline_label=baseline.config.label,
        experiment_label=experiment.config.label,
        dataset_sha256=baseline_hash,
        sample_coverage={
            "baseline_cases": len(baseline_ids),
            "experiment_cases": len(experiment_ids),
            "common_cases": len(baseline_ids),
            "expected_turns": expected_turns,
            "baseline_observed_turns": sum(
                len(item.turns) for item in baseline.results
            ),
            "experiment_observed_turns": sum(
                len(item.turns) for item in experiment.results
            ),
            "summary_source": "recalculated_from_results",
        },
        quality_gate={
            "baseline_passed": baseline_gate,
            "experiment_passed": experiment_gate,
            "regressed": baseline_gate and not experiment_gate,
            "improved": not baseline_gate and experiment_gate,
        },
        metrics=metric_deltas,
        regressions=[
            item
            for item in transitions
            if item.change == "turn_regression"
        ],
        improvements=[
            item
            for item in transitions
            if item.change == "turn_improvement"
        ],
    )


def _agent_results_by_id(
    report: AgentRunReport,
) -> dict[str, AgentCaseResult]:
    indexed = {item.case.id: item for item in report.results}
    if len(indexed) != len(report.results):
        raise AnalysisError("Agent 报告中存在重复样本 ID")
    return indexed


def _agent_turns_by_index(
    result: AgentCaseResult,
) -> dict[int, AgentTurnResult]:
    indexed = {item.turn_index: item for item in result.turns}
    if len(indexed) != len(result.turns):
        raise AnalysisError(f"Agent 场景存在重复 Turn：{result.case.id}")
    for turn_index, turn in indexed.items():
        if turn_index > len(result.case.turns):
            raise AnalysisError(
                f"Agent 场景包含越界 Turn：{result.case.id}:{turn_index}"
            )
        if turn.expected != result.case.turns[turn_index - 1]:
            raise AnalysisError(
                f"Agent 结果内 Gold 与场景标签不一致："
                f"{result.case.id}:{turn_index}"
            )
    return indexed


def _agent_turn_passed(turn: AgentTurnResult | None) -> bool:
    return turn is not None and agent_turn_checks(turn)["passed"]


def _agent_turn_state(turn: AgentTurnResult | None) -> str:
    if turn is None:
        return "missing"
    observation = turn.observation
    if observation.status == "error":
        code = observation.error_code or str(
            observation.http_status or "unknown"
        )
        return f"api_error:{code}"
    checks = agent_turn_checks(turn)
    if checks["passed"]:
        return "passed"
    failed = [
        name
        for name, passed in checks.items()
        if name != "passed" and not passed
    ]
    return "failed:" + ",".join(failed)


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdef" for character in value.lower()
    )
