from __future__ import annotations

from spb_eval.metrics import calculate_metrics, fact_coverage
from spb_eval.schemas import (
    CaseResult,
    ChatObservation,
    CitationItem,
    EvalCase,
    RetrieveObservation,
    RetrievedItem,
)


def _answer_case(case_id: str, document_id: str) -> EvalCase:
    return EvalCase(
        id=case_id,
        category="direct",
        question=f"问题 {case_id}",
        expected_outcome="answer",
        gold_document_ids=[document_id],
        required_facts=[["许可条件"], ["法人资格", "企业法人"]],
    )


def test_calculate_metrics_covers_retrieval_gates_and_answers() -> None:
    good = CaseResult(
        case=_answer_case("good", "doc-good"),
        retrieval=RetrieveObservation(
            status="ok",
            client_elapsed_ms=100,
            server_elapsed_ms=80,
            results=[
                RetrievedItem(
                    rank=1,
                    document_id="other",
                    source_url="https://example.com/other",
                ),
                RetrievedItem(
                    rank=2,
                    document_id="doc-good",
                    source_url="https://example.com/good",
                ),
            ],
        ),
        chat=ChatObservation(
            status="ok",
            client_elapsed_ms=500,
            finish_reason="stop",
            answer="申请人应具有企业法人资格，并符合许可条件。",
            citations=[
                CitationItem(
                    document_id="doc-good",
                    source_url="https://example.com/good",
                )
            ],
            usage={"total_tokens": 100},
        ),
    )
    false_reject = CaseResult(
        case=_answer_case("rejected", "doc-rejected"),
        retrieval=RetrieveObservation(
            status="ok",
            client_elapsed_ms=200,
            results=[],
        ),
        chat=ChatObservation(
            status="ok",
            client_elapsed_ms=300,
            finish_reason="reranker_rejected",
        ),
    )
    correct_reject = CaseResult(
        case=EvalCase(
            id="reject",
            category="in_domain_missing",
            question="知识库没有答案的问题",
            expected_outcome="reject",
        ),
        chat=ChatObservation(
            status="ok",
            client_elapsed_ms=400,
            finish_reason="llm_rejected",
        ),
    )
    false_accept = CaseResult(
        case=EvalCase(
            id="accepted",
            category="out_of_domain",
            question="领域外问题",
            expected_outcome="reject",
        ),
        chat=ChatObservation(
            status="ok",
            client_elapsed_ms=600,
            finish_reason="stop",
            answer="不应生成的回答",
            usage={"total_tokens": 50},
        ),
    )

    summary = calculate_metrics(
        [good, false_reject, correct_reject, false_accept],
        top_k=5,
    )

    assert summary["retrieval"]["recall_at_5"] == 0.5
    assert summary["retrieval"]["recall_at_5_ci95"] == {
        "lower": 0.0945,
        "upper": 0.9055,
    }
    assert summary["retrieval"]["mrr_at_5"] == 0.25
    assert summary["gates"]["false_reject_rate"] == 0.5
    assert summary["gates"]["false_accept_rate"] == 0.5
    assert summary["answers"]["citation_gold_hit_rate"] == 1.0
    assert summary["answers"]["required_fact_coverage"] == 0.5
    assert summary["efficiency"]["reported_total_tokens"] == 150
    assert summary["efficiency"]["chat_latency_ms"]["p95"] == 600
    assert summary["cases"]["outcomes"] == {"answer": 2, "reject": 2}
    assert set(summary["slices"]["category"]) == {
        "direct",
        "in_domain_missing",
        "out_of_domain",
    }


def test_fact_coverage_ignores_markdown_and_punctuation() -> None:
    answer = "应当在每年**4月30日**前提交；不得出售、泄露或非法提供。"

    assert fact_coverage(
        answer,
        [
            ["4月30日前"],
            ["不得出售泄露或非法提供"],
        ],
    ) == 1.0
