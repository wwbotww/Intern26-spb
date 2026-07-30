from __future__ import annotations

import json
from pathlib import Path

from spb_eval.reporting import render_markdown, write_report
from spb_eval.schemas import (
    CaseResult,
    ChatObservation,
    EvalCase,
    RunConfig,
    RunReport,
)


def test_write_report_creates_json_jsonl_and_markdown(
    tmp_path: Path,
) -> None:
    case = EvalCase(
        id="case-1",
        category="out_of_domain",
        question="无关问题",
        expected_outcome="reject",
    )
    report = RunReport(
        generated_at="2026-07-30T12:00:00+00:00",
        config=RunConfig(
            label="demo report",
            base_url="http://test",
            dataset="private.jsonl",
            mode="chat",
            top_k=5,
            candidate_k=40,
            concurrency=5,
            timeout_seconds=120,
        ),
        service={"version": "0.5.0"},
        summary={
            "cases": {
                "total": 1,
                "categories": {"out_of_domain": 1},
                "api_errors": 0,
            },
            "retrieval": {
                "evaluated": 0,
                "errors": 0,
                "recall_at_5": None,
                "mrr_at_5": None,
                "gold_survival_at_5": None,
            },
            "gates": {
                "answerable_evaluated": 0,
                "unanswerable_evaluated": 1,
                "false_reject_count": 0,
                "false_reject_rate": None,
                "false_accept_count": 0,
                "false_accept_rate": 0.0,
                "finish_reason_distribution": {
                    "reranker_rejected": 1
                },
            },
            "answers": {
                "citation_evaluated": 0,
                "citation_gold_hits": 0,
                "citation_gold_hit_rate": None,
                "fact_evaluated": 0,
                "required_fact_coverage": None,
            },
            "efficiency": {
                "retrieve_latency_ms": {"p50": None, "p95": None},
                "chat_latency_ms": {"p50": 50, "p95": 50},
                "reported_total_tokens": 0,
                "rejection_rate": 1.0,
            },
        },
        results=[
            CaseResult(
                case=case,
                chat=ChatObservation(
                    status="ok",
                    client_elapsed_ms=50,
                    finish_reason="reranker_rejected",
                ),
            )
        ],
    )

    output = write_report(report, tmp_path)

    assert (output / "run.json").is_file()
    assert (output / "cases.jsonl").is_file()
    assert (output / "summary.md").is_file()
    assert json.loads(
        (output / "run.json").read_text(encoding="utf-8")
    )["config"]["label"] == "demo report"
    markdown = render_markdown(report)
    assert "错误回答率" in markdown
    assert "reranker_rejected" in markdown
