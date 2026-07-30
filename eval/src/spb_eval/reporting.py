from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .schemas import RunReport


def _display(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _safe_label(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return normalized or "eval"


def render_markdown(report: RunReport) -> str:
    summary = report.summary
    retrieval = summary["retrieval"]
    gates = summary["gates"]
    answers = summary["answers"]
    efficiency = summary["efficiency"]
    top_k = report.config.top_k

    incorrect = []
    for result in report.results:
        chat = result.chat
        if chat is None or chat.status != "ok":
            continue
        rejected = chat.finish_reason in {
            "no_context",
            "reranker_rejected",
            "llm_rejected",
        }
        expected_reject = result.case.expected_outcome == "reject"
        if rejected != expected_reject:
            incorrect.append(
                (
                    result.case.id,
                    result.case.category,
                    result.case.expected_outcome,
                    chat.finish_reason,
                )
            )

    lines = [
        f"# RAG Eval：{report.config.label}",
        "",
        f"- 生成时间：`{report.generated_at}`",
        f"- 数据集：`{report.config.dataset}`",
        f"- API：`{report.config.base_url}`",
        f"- 模式：`{report.config.mode}`",
        f"- Git commit：`{report.config.git_commit}`",
        f"- 服务版本：`{report.service.get('version', 'unknown')}`",
        "",
        "## 核心结果",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
        (
            f"| Recall@{top_k} | "
            f"{_display(retrieval[f'recall_at_{top_k}'])} |"
        ),
        (
            f"| MRR@{top_k} | "
            f"{_display(retrieval[f'mrr_at_{top_k}'])} |"
        ),
        (
            "| 可回答问题错误拒答率 | "
            f"{_display(gates['false_reject_rate'])} |"
        ),
        (
            "| 无答案问题错误回答率 | "
            f"{_display(gates['false_accept_rate'])} |"
        ),
        (
            "| 引用 Gold 命中率 | "
            f"{_display(answers['citation_gold_hit_rate'])} |"
        ),
        (
            "| 必需事实覆盖率 | "
            f"{_display(answers['required_fact_coverage'])} |"
        ),
        (
            "| 检索延迟 P50 / P95 | "
            f"{_display(efficiency['retrieve_latency_ms']['p50'])} / "
            f"{_display(efficiency['retrieve_latency_ms']['p95'])} ms |"
        ),
        (
            "| 问答延迟 P50 / P95 | "
            f"{_display(efficiency['chat_latency_ms']['p50'])} / "
            f"{_display(efficiency['chat_latency_ms']['p95'])} ms |"
        ),
        "",
        "## 样本与路由",
        "",
        f"- 总样本：{summary['cases']['total']}",
        f"- API 错误样本：{summary['cases']['api_errors']}",
        (
            "- finish_reason：`"
            + json.dumps(
                gates["finish_reason_distribution"],
                ensure_ascii=False,
                sort_keys=True,
            )
            + "`"
        ),
        f"- DeepSeek 报告 Token：{efficiency['reported_total_tokens']}",
        "",
        "## 需要复核",
        "",
    ]
    if not incorrect:
        lines.append("没有发现与预期回答/拒答标签冲突的样本。")
    else:
        lines.extend(
            [
                "| 样本 | 分类 | 预期 | 实际 finish_reason |",
                "|---|---|---|---|",
            ]
        )
        lines.extend(
            f"| {case_id} | {category} | {expected} | {actual} |"
            for case_id, category, expected, actual in incorrect
        )
    lines.append("")
    return "\n".join(lines)


def write_report(report: RunReport, output_root: Path) -> Path:
    timestamp = datetime.fromisoformat(
        report.generated_at
    ).strftime("%Y%m%d-%H%M%S")
    output_dir = output_root / (
        f"{timestamp}-{_safe_label(report.config.label)}"
    )
    output_dir.mkdir(parents=True, exist_ok=False)

    (output_dir / "run.json").write_text(
        json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    with (output_dir / "cases.jsonl").open(
        "w",
        encoding="utf-8",
    ) as handle:
        for result in report.results:
            handle.write(
                json.dumps(
                    result.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
    (output_dir / "summary.md").write_text(
        render_markdown(report),
        encoding="utf-8",
    )
    return output_dir
