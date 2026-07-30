from __future__ import annotations

import json
from pathlib import Path

from spb_eval.cli import main
from spb_eval.metrics import calculate_metrics
from spb_eval.schemas import (
    CaseResult,
    EvalCase,
    RetrieveObservation,
    RetrievedItem,
    RunConfig,
    RunReport,
)


def _write_report(path: Path, label: str) -> None:
    results = [
        CaseResult(
            case=EvalCase(
                id="answer",
                category="direct",
                question="可回答问题",
                expected_outcome="answer",
                gold_document_ids=["gold"],
            ),
            retrieval=RetrieveObservation(
                status="ok",
                client_elapsed_ms=10,
                results=[
                    RetrievedItem(
                        rank=1,
                        document_id="gold",
                        source_url="https://example.com/gold",
                        rerank_score=0.9,
                    )
                ],
            ),
        ),
        CaseResult(
            case=EvalCase(
                id="reject",
                category="out_of_domain",
                question="无答案问题",
                expected_outcome="reject",
            ),
            retrieval=RetrieveObservation(
                status="ok",
                client_elapsed_ms=10,
                results=[
                    RetrievedItem(
                        rank=1,
                        document_id="noise",
                        source_url="https://example.com/noise",
                        rerank_score=0.1,
                    )
                ],
            ),
        ),
    ]
    report = RunReport(
        generated_at="2026-07-30T12:00:00+00:00",
        config=RunConfig(
            label=label,
            base_url="http://test",
            dataset="cases.jsonl",
            mode="retrieve",
            top_k=5,
            candidate_k=40,
            concurrency=5,
            timeout_seconds=10,
        ),
        summary=calculate_metrics(results, top_k=5),
        results=results,
    )
    path.write_text(
        report.model_dump_json(indent=2),
        encoding="utf-8",
    )


def test_threshold_scan_and_compare_cli(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    experiment = tmp_path / "experiment.json"
    _write_report(baseline, "baseline")
    _write_report(experiment, "experiment")
    threshold_output = tmp_path / "threshold"
    compare_output = tmp_path / "compare"

    threshold_exit = main(
        [
            "threshold-scan",
            "--report",
            str(experiment),
            "--output-dir",
            str(threshold_output),
            "--threshold",
            "0.5",
        ]
    )
    compare_exit = main(
        [
            "compare",
            "--baseline",
            str(baseline),
            "--experiment",
            str(experiment),
            "--output-dir",
            str(compare_output),
        ]
    )

    assert threshold_exit == 0
    threshold_dirs = list(threshold_output.iterdir())
    assert len(threshold_dirs) == 1
    assert (threshold_dirs[0] / "threshold-scan.json").is_file()
    assert (threshold_dirs[0] / "threshold-scan.md").is_file()
    comparison_dirs = list(compare_output.iterdir())
    assert compare_exit == 0
    assert len(comparison_dirs) == 1
    assert (comparison_dirs[0] / "comparison.json").is_file()
    assert json.loads(
        (comparison_dirs[0] / "comparison.json").read_text(
            encoding="utf-8"
        )
    )["baseline_label"] == "baseline"
