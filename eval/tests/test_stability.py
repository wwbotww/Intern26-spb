from __future__ import annotations

from datetime import UTC, datetime

from spb_eval.analysis import analyze_stability
from spb_eval.metrics import calculate_metrics
from spb_eval.schemas import (
    CaseResult,
    ChatObservation,
    CitationItem,
    EvalCase,
    RunConfig,
    RunReport,
)


def _report(label: str, answer: str) -> RunReport:
    case = EvalCase(
        id="a1",
        category="direct_answer",
        question="问题",
        expected_outcome="answer",
        gold_document_ids=["gold"],
        required_facts=[["事实"]],
    )
    results = [
        CaseResult(
            case=case,
            chat=ChatObservation(
                status="ok",
                client_elapsed_ms=10,
                finish_reason="stop",
                answer=answer,
                citations=[
                    CitationItem(
                        document_id="gold",
                        source_url="https://example.com/gold",
                    )
                ],
            ),
        )
    ]
    return RunReport(
        generated_at=datetime.now(UTC).isoformat(),
        config=RunConfig(
            label=label,
            base_url="http://test",
            dataset="frozen.jsonl",
            mode="chat",
            top_k=5,
            candidate_k=40,
            concurrency=5,
            timeout_seconds=10,
        ),
        summary=calculate_metrics(results, top_k=5),
        results=results,
    )


def test_stability_separates_quality_from_exact_wording() -> None:
    report = analyze_stability(
        [_report("one", "事实 A"), _report("two", "事实 B")],
        source_reports=["one.json", "two.json"],
    )

    assert report["quality_stable"] is True
    assert report["consistency"]["finish_reason"]["rate"] == 1.0
    assert report["consistency"]["citation_set"]["rate"] == 1.0
    assert report["consistency"]["exact_answer"]["rate"] == 0.0
