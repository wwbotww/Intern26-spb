from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .metrics import fact_coverage, gold_rank, is_gold_source, is_rejected
from .schemas import (
    CaseResult,
    ComparisonReport,
    RunReport,
    ThresholdScanReport,
)


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


def _review_issues(
    report: RunReport,
) -> list[tuple[CaseResult, list[str]]]:
    queued: list[tuple[CaseResult, list[str]]] = []
    top_k = report.config.top_k
    for result in report.results:
        issues: list[str] = []
        retrieval = result.retrieval
        chat = result.chat
        if retrieval is not None:
            if retrieval.status == "error":
                issues.append(f"检索 API 错误：{retrieval.error}")
            elif (
                result.case.expected_outcome == "answer"
                and gold_rank(
                    retrieval.results,
                    result.case,
                    top_k=top_k,
                )
                is None
            ):
                issues.append(f"Gold 来源未进入 Top {top_k}")
        if chat is not None:
            if chat.status == "error":
                issues.append(f"问答 API 错误：{chat.error}")
            else:
                rejected = is_rejected(chat.finish_reason)
                if (
                    result.case.expected_outcome == "answer"
                    and rejected
                ):
                    issues.append(
                        f"可回答问题被拒答：{chat.finish_reason}"
                    )
                elif (
                    result.case.expected_outcome == "reject"
                    and not rejected
                ):
                    issues.append(
                        f"无答案问题被回答：{chat.finish_reason}"
                    )
                if (
                    result.case.expected_outcome == "answer"
                    and not rejected
                    and not any(
                        is_gold_source(source, result.case)
                        for source in chat.citations
                    )
                ):
                    issues.append("生成答案未引用 Gold 来源")
                if (
                    result.case.expected_outcome == "answer"
                    and result.case.required_facts
                    and not rejected
                    and fact_coverage(
                        chat.answer,
                        result.case.required_facts,
                    )
                    < 1
                ):
                    issues.append("必需事实覆盖不完整")
        if issues:
            queued.append((result, issues))
    return queued


def render_review_queue(report: RunReport) -> str:
    queued = _review_issues(report)
    lines = [
        f"# 人工复核队列：{report.config.label}",
        "",
        f"- 总样本：{len(report.results)}",
        f"- 待复核：{len(queued)}",
        "- 说明：该文件为人工判断辅助，不会自动回写数据集。",
        "",
    ]
    if not queued:
        lines.append("当前没有自动规则标记的待复核样本。")
        lines.append("")
        return "\n".join(lines)

    for result, issues in queued:
        case = result.case
        lines.extend(
            [
                f"## {case.id}",
                "",
                f"- 分类：`{case.category}`",
                f"- 预期：`{case.expected_outcome}`",
                "- 问题：" + case.question,
                "- 自动标记：" + "；".join(issues),
                (
                    "- Gold document IDs：`"
                    + json.dumps(
                        case.gold_document_ids,
                        ensure_ascii=False,
                    )
                    + "`"
                ),
                (
                    "- Gold URLs：`"
                    + json.dumps(
                        case.gold_source_urls,
                        ensure_ascii=False,
                    )
                    + "`"
                ),
                (
                    "- 必需事实：`"
                    + json.dumps(
                        case.required_facts,
                        ensure_ascii=False,
                    )
                    + "`"
                ),
            ]
        )
        if result.chat is not None:
            lines.append(
                f"- finish_reason：`{result.chat.finish_reason or 'N/A'}`"
            )
            lines.append("- 回答：")
            lines.extend(
                f"> {line}" if line else ">"
                for line in (result.chat.answer or "N/A").splitlines()
            )
            lines.append("- 引用：")
            if result.chat.citations:
                lines.extend(
                    (
                        f"  - `{citation.document_id}` "
                        f"{citation.title} {citation.source_url}"
                    )
                    for citation in result.chat.citations
                )
            else:
                lines.append("  - 无")
        lines.extend(
            [
                "- 人工结论："
                "`[ ] 正确  [ ] 部分正确  [ ] 错误  [ ] 应拒答`",
                "- 备注：",
                "",
            ]
        )
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
    (output_dir / "review-queue.md").write_text(
        render_review_queue(report),
        encoding="utf-8",
    )
    return output_dir


def render_threshold_markdown(report: ThresholdScanReport) -> str:
    constraints = report.constraints
    lines = [
        "# Reranker 阈值扫描",
        "",
        f"- 来源报告：`{report.source_report}`",
        (
            "- 推荐阈值：`"
            + (
                str(report.recommended_threshold)
                if report.recommended_threshold is not None
                else "N/A"
            )
            + "`"
        ),
        (
            "- 满足全部约束："
            + ("是" if report.recommendation_constraints_met else "否")
        ),
        f"- 推荐说明：{report.recommendation_reason}",
        (
            "- 约束：错误回答率 ≤ "
            f"{constraints['max_false_accept_rate']:.4f}，"
            "错误拒答率 ≤ "
            f"{constraints['max_false_reject_rate']:.4f}，"
            "Gold 存活率 ≥ "
            f"{constraints['min_gold_survival_rate']:.4f}"
        ),
        "",
        "## 数据覆盖",
        "",
        "| 项目 | 数量 |",
        "|---|---:|",
    ]
    lines.extend(
        f"| {name} | {value} |"
        for name, value in report.coverage.items()
    )
    lines.extend(
        [
            "",
            "## 扫描结果",
            "",
            (
                "| 阈值 | 错误拒答率 | 错误回答率 | "
                "Gold 存活率 | 查询通过率 | 约束 |"
            ),
            "|---:|---:|---:|---:|---:|---|",
        ]
    )
    lines.extend(
        (
            f"| {point.threshold:.4f} | "
            f"{_display(point.false_reject_rate)} | "
            f"{_display(point.false_accept_rate)} | "
            f"{_display(point.gold_survival_rate)} | "
            f"{_display(point.accepted_query_rate)} | "
            f"{'通过' if point.constraints_met else '未通过'} |"
        )
        for point in report.points
    )
    lines.append("")
    return "\n".join(lines)


def write_threshold_report(
    report: ThresholdScanReport,
    output_root: Path,
) -> tuple[Path, Path]:
    timestamp = datetime.fromisoformat(
        report.generated_at
    ).strftime("%Y%m%d-%H%M%S")
    output_dir = output_root / f"{timestamp}-threshold-scan"
    output_dir.mkdir(parents=True, exist_ok=False)
    json_path = output_dir / "threshold-scan.json"
    markdown_path = output_dir / "threshold-scan.md"
    json_path.write_text(
        json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_threshold_markdown(report),
        encoding="utf-8",
    )
    return json_path, markdown_path


def render_comparison_markdown(report: ComparisonReport) -> str:
    lines = [
        (
            f"# Eval 对比：{report.baseline_label} → "
            f"{report.experiment_label}"
        ),
        "",
        f"- Baseline：`{report.baseline_report}`",
        f"- Experiment：`{report.experiment_report}`",
        f"- 共同样本：{report.sample_coverage['common_cases']}",
        "",
        "## 指标差异",
        "",
        "| 指标 | Baseline | Experiment | 差值 | 判断 |",
        "|---|---:|---:|---:|---|",
    ]
    for metric in report.metrics:
        if metric.improved is True:
            judgment = "改善"
        elif metric.improved is False:
            judgment = "退化"
        elif metric.baseline is None or metric.experiment is None:
            judgment = "不可比"
        else:
            judgment = "持平"
        lines.append(
            f"| {metric.metric} | {_display(metric.baseline)} | "
            f"{_display(metric.experiment)} | "
            f"{_display(metric.delta)} | {judgment} |"
        )

    lines.extend(
        [
            "",
            f"## 回归样本（{len(report.regressions)}）",
            "",
        ]
    )
    if report.regressions:
        lines.extend(
            (
                f"- `{item.case_id}` [{item.category}] "
                f"{item.change}：{item.baseline} → {item.experiment}"
            )
            for item in report.regressions
        )
    else:
        lines.append("未发现逐样本回归。")

    lines.extend(
        [
            "",
            f"## 改善样本（{len(report.improvements)}）",
            "",
        ]
    )
    if report.improvements:
        lines.extend(
            (
                f"- `{item.case_id}` [{item.category}] "
                f"{item.change}：{item.baseline} → {item.experiment}"
            )
            for item in report.improvements
        )
    else:
        lines.append("未发现逐样本改善。")
    lines.append("")
    return "\n".join(lines)


def write_comparison_report(
    report: ComparisonReport,
    output_root: Path,
) -> Path:
    timestamp = datetime.fromisoformat(
        report.generated_at
    ).strftime("%Y%m%d-%H%M%S")
    output_dir = output_root / (
        f"{timestamp}-{_safe_label(report.baseline_label)}-vs-"
        f"{_safe_label(report.experiment_label)}"
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "comparison.json").write_text(
        json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (output_dir / "comparison.md").write_text(
        render_comparison_markdown(report),
        encoding="utf-8",
    )
    return output_dir
