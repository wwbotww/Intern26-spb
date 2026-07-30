from __future__ import annotations

from datetime import UTC, datetime

import pytest

from spb_eval.analysis import (
    AnalysisError,
    compare_reports,
    scan_thresholds,
)
from spb_eval.metrics import calculate_metrics
from spb_eval.schemas import (
    CaseResult,
    ChatObservation,
    CitationItem,
    EvalCase,
    RetrieveObservation,
    RetrievedItem,
    RunConfig,
    RunReport,
)


def _case(
    case_id: str,
    expected_outcome: str,
    *,
    document_id: str = "",
) -> EvalCase:
    return EvalCase(
        id=case_id,
        category=(
            "direct"
            if expected_outcome == "answer"
            else "in_domain_missing"
        ),
        question=f"问题 {case_id}",
        expected_outcome=expected_outcome,
        gold_document_ids=[document_id] if document_id else [],
    )


def _retrieval(
    document_id: str,
    rerank_score: float,
) -> RetrieveObservation:
    return RetrieveObservation(
        status="ok",
        client_elapsed_ms=10,
        results=[
            RetrievedItem(
                rank=1,
                document_id=document_id,
                source_url=f"https://example.com/{document_id}",
                rerank_score=rerank_score,
            )
        ],
    )


def _report(label: str, results: list[CaseResult]) -> RunReport:
    top_k = 5
    return RunReport(
        generated_at=datetime.now(UTC).isoformat(),
        config=RunConfig(
            label=label,
            base_url="http://test",
            dataset="cases.jsonl",
            mode="all",
            top_k=top_k,
            candidate_k=40,
            concurrency=5,
            timeout_seconds=10,
        ),
        summary=calculate_metrics(results, top_k=top_k),
        results=results,
    )


def test_threshold_scan_calculates_tradeoff_and_prefers_highest_safe() -> None:
    results = [
        CaseResult(
            case=_case("a1", "answer", document_id="gold-a1"),
            retrieval=_retrieval("gold-a1", 0.9),
        ),
        CaseResult(
            case=_case("a2", "answer", document_id="gold-a2"),
            retrieval=_retrieval("gold-a2", 0.4),
        ),
        CaseResult(
            case=_case("r1", "reject"),
            retrieval=_retrieval("noise-r1", 0.2),
        ),
        CaseResult(
            case=_case("r2", "reject"),
            retrieval=_retrieval("noise-r2", 0.7),
        ),
    ]

    scan = scan_thresholds(
        _report("shadow", results),
        source_report="run.json",
        thresholds=[0.3, 0.5, 0.8],
        max_false_accept_rate=0.5,
        max_false_reject_rate=0.5,
        min_gold_survival_rate=0.5,
    )

    middle = scan.points[1]
    assert middle.threshold == 0.5
    assert middle.false_reject_rate == 0.5
    assert middle.false_accept_rate == 0.5
    assert middle.gold_survival_rate == 0.5
    assert scan.recommended_threshold == 0.8
    assert scan.recommendation_constraints_met is True
    assert scan.coverage["usable_cases"] == 4


def test_threshold_scan_requires_shadow_mode_scores() -> None:
    result = CaseResult(
        case=_case("a1", "answer", document_id="gold"),
        retrieval=RetrieveObservation(
            status="ok",
            client_elapsed_ms=10,
            results=[
                RetrievedItem(
                    rank=1,
                    document_id="gold",
                    source_url="https://example.com/gold",
                )
            ],
        ),
    )

    with pytest.raises(AnalysisError, match="rerank_score"):
        scan_thresholds(
            _report("baseline", [result]),
            source_report="run.json",
            thresholds=[0.5],
            max_false_accept_rate=0.1,
            max_false_reject_rate=0.15,
            min_gold_survival_rate=0.8,
        )


def test_threshold_scan_does_not_recommend_with_api_errors() -> None:
    results = [
        CaseResult(
            case=_case("a1", "answer", document_id="gold"),
            retrieval=_retrieval("gold", 0.9),
        ),
        CaseResult(
            case=_case("r1", "reject"),
            retrieval=_retrieval("noise", 0.1),
        ),
        CaseResult(
            case=_case("r2", "reject"),
            retrieval=RetrieveObservation(
                status="error",
                client_elapsed_ms=10,
                error="timeout",
            ),
        ),
    ]

    scan = scan_thresholds(
        _report("shadow", results),
        source_report="run.json",
        thresholds=[0.5],
        max_false_accept_rate=0.1,
        max_false_reject_rate=0.15,
        min_gold_survival_rate=0.8,
    )

    assert scan.recommended_threshold is None
    assert scan.recommendation_constraints_met is False
    assert scan.coverage["api_errors"] == 1
    assert "不推荐阈值" in scan.recommendation_reason


def test_compare_reports_finds_case_improvements_and_metric_deltas() -> None:
    answer = _case("answer", "answer", document_id="gold")
    citation = _case("citation", "answer", document_id="gold-citation")
    reject = _case("reject", "reject")
    baseline = _report(
        "baseline",
        [
            CaseResult(
                case=answer,
                retrieval=_retrieval("noise", 0.8),
                chat=ChatObservation(
                    status="ok",
                    client_elapsed_ms=100,
                    finish_reason="reranker_rejected",
                ),
            ),
            CaseResult(
                case=citation,
                retrieval=_retrieval("gold-citation", 0.8),
                chat=ChatObservation(
                    status="ok",
                    client_elapsed_ms=100,
                    finish_reason="stop",
                    citations=[
                        CitationItem(
                            document_id="noise",
                            source_url="https://example.com/noise",
                        )
                    ],
                ),
            ),
            CaseResult(
                case=reject,
                chat=ChatObservation(
                    status="ok",
                    client_elapsed_ms=100,
                    finish_reason="stop",
                ),
            ),
        ],
    )
    experiment = _report(
        "experiment",
        [
            CaseResult(
                case=answer,
                retrieval=_retrieval("gold", 0.9),
                chat=ChatObservation(
                    status="ok",
                    client_elapsed_ms=90,
                    finish_reason="stop",
                    citations=[
                        CitationItem(
                            document_id="gold",
                            source_url="https://example.com/gold",
                        )
                    ],
                ),
            ),
            CaseResult(
                case=citation,
                retrieval=_retrieval("gold-citation", 0.9),
                chat=ChatObservation(
                    status="ok",
                    client_elapsed_ms=90,
                    finish_reason="stop",
                    citations=[
                        CitationItem(
                            document_id="gold-citation",
                            source_url=(
                                "https://example.com/gold-citation"
                            ),
                        )
                    ],
                ),
            ),
            CaseResult(
                case=reject,
                chat=ChatObservation(
                    status="ok",
                    client_elapsed_ms=90,
                    finish_reason="llm_rejected",
                ),
            ),
        ],
    )

    comparison = compare_reports(
        baseline,
        experiment,
        baseline_report="baseline.json",
        experiment_report="experiment.json",
    )

    changes = {item.change for item in comparison.improvements}
    assert "gate_improvement" in changes
    assert "retrieval_improvement" in changes
    assert "citation_improvement" in changes
    assert comparison.regressions == []
    recall = next(
        item
        for item in comparison.metrics
        if item.metric == "recall_at_5"
    )
    assert recall.delta == 0.5
    assert recall.improved is True


def test_compare_reports_requires_identical_samples() -> None:
    baseline = _report(
        "baseline",
        [
            CaseResult(
                case=_case("a", "answer", document_id="gold"),
            )
        ],
    )
    experiment = _report(
        "experiment",
        [
            CaseResult(
                case=_case("b", "answer", document_id="gold"),
            )
        ],
    )

    with pytest.raises(AnalysisError, match="相同样本 ID"):
        compare_reports(
            baseline,
            experiment,
            baseline_report="baseline.json",
            experiment_report="experiment.json",
        )
