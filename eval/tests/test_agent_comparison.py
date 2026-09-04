from __future__ import annotations

import json
from pathlib import Path

import pytest

from spb_eval.analysis import (
    AnalysisError,
    compare_agent_reports,
    load_agent_run_report,
)
from spb_eval.cli import build_parser, main
from spb_eval.metrics import calculate_agent_metrics
from spb_eval.reporting import (
    render_agent_comparison_markdown,
    write_agent_comparison_report,
)
from spb_eval.schemas import (
    AgentCaseResult,
    AgentEvalCase,
    AgentEvalThresholds,
    AgentEvalTurn,
    AgentRunConfig,
    AgentRunReport,
    AgentTurnObservation,
    AgentTurnResult,
)


DATASET_HASH = "a" * 64


def _case(case_id: str) -> AgentEvalCase:
    return AgentEvalCase(
        id=case_id,
        category="single_turn_success",
        split="holdout",
        turns=[
            AgentEvalTurn(
                message=f"查询 {case_id}",
                expected_phase="completed",
                expected_intent="tracking",
                expected_next_action="complete",
                expected_result_status="success",
            )
        ],
    )


def _ok_result(
    case: AgentEvalCase,
    *,
    intent: str = "tracking",
    latency_ms: float = 20,
) -> AgentCaseResult:
    return AgentCaseResult(
        case=case,
        turns=[
            AgentTurnResult(
                turn_index=1,
                expected=case.turns[0],
                observation=AgentTurnObservation(
                    status="ok",
                    client_elapsed_ms=latency_ms,
                    phase="completed",
                    intent=intent,
                    next_action="complete",
                    result={
                        "type": intent,
                        "status": "success",
                        "data": {"type": intent},
                    },
                ),
            )
        ],
    )


def _error_result(case: AgentEvalCase) -> AgentCaseResult:
    return AgentCaseResult(
        case=case,
        turns=[
            AgentTurnResult(
                turn_index=1,
                expected=case.turns[0],
                observation=AgentTurnObservation(
                    status="error",
                    client_elapsed_ms=100,
                    http_status=503,
                    error_code="upstream_unavailable",
                    error_category="upstream_unavailable",
                    retryable=True,
                    error="依赖不可用",
                ),
            )
        ],
    )


def _report(
    label: str,
    results: list[AgentCaseResult],
    *,
    dataset_hash: str = DATASET_HASH,
) -> AgentRunReport:
    thresholds = AgentEvalThresholds()
    return AgentRunReport(
        generated_at="2026-09-04T12:00:00+00:00",
        config=AgentRunConfig(
            label=label,
            base_url="http://test",
            dataset="agent.jsonl",
            dataset_sha256=dataset_hash,
            concurrency=2,
            timeout_seconds=10,
            thresholds=thresholds,
        ),
        summary=calculate_agent_metrics(
            results,
            thresholds=thresholds,
        ),
        results=results,
    )


def test_agent_compare_finds_turn_regression_and_improvement(
    tmp_path: Path,
) -> None:
    improved_case = _case("improved")
    regressed_case = _case("regressed")
    baseline = _report(
        "baseline",
        [
            _ok_result(improved_case, intent="delivery_time"),
            _ok_result(regressed_case),
        ],
    )
    experiment = _report(
        "experiment",
        [
            _ok_result(improved_case, latency_ms=10),
            _error_result(regressed_case),
        ],
    )
    # Saved summaries may be stale; comparison must rebuild from Turn data.
    baseline.summary["routing"]["wrong_tool_rate"] = 0.0
    experiment.summary["routing"]["wrong_tool_rate"] = 1.0

    comparison = compare_agent_reports(
        baseline,
        experiment,
        baseline_report="baseline/run.json",
        experiment_report="experiment/run.json",
    )

    assert comparison.dataset_sha256 == DATASET_HASH
    assert comparison.sample_coverage["expected_turns"] == 2
    assert [item.case_id for item in comparison.improvements] == [
        "improved"
    ]
    assert [item.case_id for item in comparison.regressions] == [
        "regressed"
    ]
    assert comparison.regressions[0].experiment == (
        "api_error:upstream_unavailable"
    )
    wrong_tool = next(
        item
        for item in comparison.metrics
        if item.metric == "wrong_tool_rate"
    )
    api_errors = next(
        item
        for item in comparison.metrics
        if item.metric == "api_error_rate"
    )
    assert wrong_tool.improved is True
    assert api_errors.improved is False

    markdown = render_agent_comparison_markdown(comparison)
    assert "Turn 回归（1）" in markdown
    assert "Turn 改善（1）" in markdown
    output = write_agent_comparison_report(comparison, tmp_path)
    assert (output / "agent-comparison.json").is_file()
    assert (output / "agent-comparison.md").read_text(
        encoding="utf-8"
    ) == markdown


@pytest.mark.parametrize("dataset_hash", ["", "b" * 64])
def test_agent_compare_rejects_missing_or_different_dataset_hash(
    dataset_hash: str,
) -> None:
    case = _case("same")
    baseline = _report("baseline", [_ok_result(case)])
    experiment = _report(
        "experiment",
        [_ok_result(case)],
        dataset_hash=dataset_hash,
    )

    with pytest.raises(AnalysisError, match="dataset_sha256"):
        compare_agent_reports(
            baseline,
            experiment,
            baseline_report="baseline.json",
            experiment_report="experiment.json",
        )


def test_agent_compare_rejects_changed_gold_labels() -> None:
    baseline_case = _case("same")
    experiment_case = _case("same")
    experiment_case.turns[0].expected_result_status = "no_match"
    baseline = _report("baseline", [_ok_result(baseline_case)])
    experiment = _report(
        "experiment",
        [_ok_result(experiment_case)],
    )

    with pytest.raises(AnalysisError, match="样本标签不一致"):
        compare_agent_reports(
            baseline,
            experiment,
            baseline_report="baseline.json",
            experiment_report="experiment.json",
        )


def test_agent_compare_cli_loads_validated_reports(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _case("cli")
    baseline = _report("baseline", [_error_result(case)])
    experiment = _report("experiment", [_ok_result(case)])
    baseline_path = tmp_path / "baseline.json"
    experiment_path = tmp_path / "experiment.json"
    baseline_path.write_text(
        baseline.model_dump_json(indent=2),
        encoding="utf-8",
    )
    experiment_path.write_text(
        experiment.model_dump_json(indent=2),
        encoding="utf-8",
    )
    output_root = tmp_path / "comparisons"

    parsed = build_parser().parse_args(
        [
            "agent-compare",
            "--baseline",
            str(baseline_path),
            "--experiment",
            str(experiment_path),
            "--output-dir",
            str(output_root),
        ]
    )
    assert parsed.command == "agent-compare"
    assert main(
        [
            "agent-compare",
            "--baseline",
            str(baseline_path),
            "--experiment",
            str(experiment_path),
            "--output-dir",
            str(output_root),
        ]
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    report_dir = Path(payload["report_dir"])
    assert payload["status"] == "ok"
    assert load_agent_run_report(baseline_path).config.label == "baseline"
    comparison = json.loads(
        (report_dir / "agent-comparison.json").read_text(
            encoding="utf-8"
        )
    )
    assert comparison["improvements"][0]["case_id"] == "cli"
