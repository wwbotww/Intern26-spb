from __future__ import annotations

from datetime import UTC, datetime

import pytest

from spb_eval.analysis import AnalysisError, recalculate_report
from spb_eval.metrics import calculate_metrics
from spb_eval.schemas import (
    CaseResult,
    ChatObservation,
    EvalCase,
    RunConfig,
    RunReport,
)


def _case(*, question: str = "问题", fact: str = "旧事实") -> EvalCase:
    return EvalCase(
        id="case-1",
        category="direct_answer",
        question=question,
        expected_outcome="answer",
        gold_document_ids=["gold"],
        required_facts=[[fact]],
    )


def _report(case: EvalCase) -> RunReport:
    results = [
        CaseResult(
            case=case,
            chat=ChatObservation(
                status="ok",
                client_elapsed_ms=10,
                finish_reason="stop",
                answer="答案包含新事实",
            ),
        )
    ]
    return RunReport(
        generated_at=datetime.now(UTC).isoformat(),
        config=RunConfig(
            label="before",
            base_url="http://test",
            dataset="before.jsonl",
            mode="chat",
            top_k=5,
            candidate_k=40,
            concurrency=1,
            timeout_seconds=10,
        ),
        summary=calculate_metrics(results, top_k=5),
        results=results,
    )


def test_recalculate_reuses_observations_with_new_labels() -> None:
    recalculated = recalculate_report(
        _report(_case()),
        cases=[_case(fact="新事实")],
        dataset="after.jsonl",
        label="after",
    )

    assert recalculated.config.label == "after"
    assert recalculated.config.dataset == "after.jsonl"
    assert recalculated.results[0].chat is not None
    assert recalculated.results[0].chat.answer == "答案包含新事实"
    assert recalculated.summary["answers"]["required_fact_coverage"] == 1.0


def test_recalculate_rejects_changed_question() -> None:
    with pytest.raises(AnalysisError, match="问题文本或请求过滤条件已变化"):
        recalculate_report(
            _report(_case()),
            cases=[_case(question="另一个问题")],
            dataset="after.jsonl",
            label="after",
        )
