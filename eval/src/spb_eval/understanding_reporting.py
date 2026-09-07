from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .understanding_dataset import (
    build_requests,
    load_observations,
    load_understanding_cases,
)
from .understanding_metrics import (
    METRIC_VERSION,
    UnderstandingThresholds,
    calculate_understanding_metrics,
)


def score_understanding(
    dataset: Path,
    split: str,
    observations: Path,
    thresholds: UnderstandingThresholds | None = None,
) -> dict:
    dataset_hash, cases = load_understanding_cases(dataset, split)
    manifest, by_id = load_observations(
        observations, build_requests(dataset_hash, cases)
    )
    summary, rows = calculate_understanding_metrics(cases, by_id, thresholds)
    return {
        "schema_version": "qu-report-v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset_sha256": dataset_hash,
        "split": split,
        "observations_sha256": hashlib.sha256(
            observations.read_bytes()
        ).hexdigest(),
        "manifest": manifest.model_dump(mode="json"),
        "summary": summary,
        "cases": rows,
        "limitations": [
            "Component evaluation with supplied context, not end-to-end workflow or merged-state slot quality.",
            "Slot values are pseudonymized fingerprints, not guaranteed irreversible anonymization.",
            "Known tokens exclude unknown usage and unobserved requests; not a billing total.",
            "Synthetic development results are not unseen holdout or production accuracy.",
        ],
    }


def compare_understanding(
    dataset: Path,
    split: str,
    baseline: Path,
    experiment: Path,
    thresholds: UnderstandingThresholds | None = None,
) -> dict:
    # Both are independently validated and recomputed against the same full Gold.
    base = score_understanding(dataset, split, baseline, thresholds)
    exp = score_understanding(dataset, split, experiment, thresholds)
    metrics = []
    for path, higher_better in (
        ("intent.macro_f1", True),
        ("slots.micro_f1", True),
        ("missing_slots.accuracy", True),
        ("model.call_rate", None),
        ("model.failure_rate_per_call", False),
        ("component_latency.p95_ms", False),
    ):
        section, key = path.split(".")
        before, after = (
            base["summary"][section][key],
            exp["summary"][section][key],
        )
        delta = (
            after - before
            if before is not None and after is not None
            else None
        )
        metrics.append(
            {
                "metric": path,
                "baseline": before,
                "experiment": after,
                "delta": delta,
                "improved": None
                if delta is None or delta == 0 or higher_better is None
                else (delta > 0) == higher_better,
            }
        )
    transitions = []
    for before, after in zip(base["cases"], exp["cases"], strict=True):
        transitions.append(
            {
                "id": before["id"],
                "transition": "improved"
                if not before["passed"] and after["passed"]
                else "regressed"
                if before["passed"] and not after["passed"]
                else "unchanged",
                "baseline_reasons": before["reasons"],
                "experiment_reasons": after["reasons"],
            }
        )
    return {
        "schema_version": "qu-comparison-v1",
        "metric_version": METRIC_VERSION,
        "dataset_sha256": base["dataset_sha256"],
        "split": split,
        "baseline": base,
        "experiment": exp,
        "metrics": metrics,
        "transitions": transitions,
        "same_observation_artifact": base["observations_sha256"]
        == exp["observations_sha256"],
        "configuration_changes": {
            key: {
                "baseline": base["manifest"][key],
                "experiment": exp["manifest"][key],
            }
            for key in base["manifest"]
            if key != "created_at"
            and base["manifest"][key] != exp["manifest"][key]
        },
    }


def _number(value):
    return "N/A" if value is None else f"{value:.4f}"


def write_understanding_report(report: dict, output_dir: Path) -> Path:
    output = (
        output_dir
        / f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"
    )
    output.mkdir(parents=True, exist_ok=False)
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    if report["schema_version"] == "qu-report-v1":
        summary = report["summary"]
        lines = [
            "# Query Understanding 组件评测",
            "",
            "不代表完整 Workflow、未见 holdout 或生产准确率。",
            "",
            f"- Split: `{report['split']}`；模式: `{report['manifest']['mode']}`；传输: `{report['manifest']['transport']}`",
            f"- 数据 SHA256: `{report['dataset_sha256']}`",
            f"- 样本: {summary['cases']['total']}；完整通过: {summary['cases']['passed']}；门禁: {summary['quality_gate']['passed']}",
            f"- Intent Macro-F1: {_number(summary['intent']['macro_f1'])}",
            f"- Joint hard-slot micro-F1: {_number(summary['slots']['micro_f1'])}",
            f"- Missing-slot accuracy: {_number(summary['missing_slots']['accuracy'])}",
            f"- 模型调用: {summary['model']['calls']}；已观测调用失败率: {_number(summary['model']['failure_rate_per_call'])}",
            f"- 正常模型 unknown: {summary['model']['normal_model_unknown']}；失败回退 unknown: {summary['model']['failure_fallback_unknown']}",
            f"- 已知 token 合计: {summary['model']['usage']['total_tokens']['known_tokens']}；用量未知调用: {summary['model']['usage']['total_tokens']['unknown_calls']}；未观测样本: {summary['model']['unobserved_case_rows']}",
            "",
            "## 分类别 Intent",
            "",
            "| 类别 | support | TP | FP | FN | F1 |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        for name, scores in summary["intent"]["per_class"].items():
            lines.append(
                f"| {name} | {scores['support']} | {scores['tp']} | {scores['fp']} | {scores['fn']} | {_number(scores['f1'])} |"
            )
        lines += [
            "",
            "## 失败复核",
            "",
            "| 样本 | 失败原因 |",
            "| --- | --- |",
        ]
        for row in report["cases"]:
            if not row["passed"]:
                lines.append(f"| {row['id']} | {', '.join(row['reasons'])} |")
        queue = [
            row
            for row in report["cases"]
            if not row["passed"] or row["model_outcome"] == "failure"
        ]
    else:
        lines = [
            "# Query Understanding 同样本对照",
            "",
            f"数据 SHA256: `{report['dataset_sha256']}`",
            "",
            f"同一 observation artifact: {report['same_observation_artifact']}（为 True 时仅是管线自检）",
            "",
            "| 指标 | Baseline | Experiment | Delta |",
            "| --- | ---: | ---: | ---: |",
        ]
        for metric in report["metrics"]:
            lines.append(
                f"| {metric['metric']} | {_number(metric['baseline'])} | {_number(metric['experiment'])} | {_number(metric['delta'])} |"
            )
        lines += [
            "",
            "配置差异、完整分母及逐样本回归／改善见 report.json；这不是单变量因果证明。",
        ]
        queue = [
            row
            for row in report["transitions"]
            if row["transition"] == "regressed"
        ]
    (output / "report.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    (output / "review-queue.json").write_text(
        json.dumps(queue, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output
