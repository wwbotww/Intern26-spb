from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from decimal import Decimal
from pathlib import Path

from .analysis import (
    AnalysisError,
    analyze_stability,
    compare_reports,
    load_run_report,
    recalculate_report,
    scan_thresholds,
)
from .client import AssistantApiClient, RagApiClient
from .dataset import (
    DatasetError,
    load_assistant_dataset,
    load_dataset,
    split_dataset,
)
from .reporting import (
    write_assistant_report,
    write_comparison_report,
    write_report,
    write_stability_report,
    write_threshold_report,
)
from .runner import (
    resolve_git_commit,
    run_assistant_evaluation,
    run_evaluation,
)
from .schemas import AssistantRunConfig, RunConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spb-eval",
        description="政策 RAG 与中国邮政理赔助手黑盒评估工具",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="运行 JSONL 评估数据集")
    run.add_argument("--dataset", type=Path, required=True)
    run.add_argument(
        "--output-dir",
        type=Path,
        default=Path("eval/reports"),
    )
    run.add_argument("--label", default="eval")
    run.add_argument(
        "--base-url",
        default=os.getenv(
            "EVAL_BASE_URL",
            "http://127.0.0.1:8080",
        ),
    )
    run.add_argument(
        "--mode",
        choices=("retrieve", "chat", "all"),
        default="all",
    )
    run.add_argument("--top-k", type=int, default=5)
    run.add_argument("--candidate-k", type=int, default=40)
    run.add_argument("--concurrency", type=int, default=5)
    run.add_argument("--timeout-seconds", type=float, default=120.0)

    assistant_run = subparsers.add_parser(
        "assistant-run",
        help="运行理赔助手 policy/device_price 黑盒评估数据集",
    )
    assistant_run.add_argument("--dataset", type=Path, required=True)
    assistant_run.add_argument(
        "--output-dir",
        type=Path,
        default=Path("eval/reports"),
    )
    assistant_run.add_argument("--label", default="assistant-eval")
    assistant_run.add_argument(
        "--base-url",
        default=os.getenv(
            "EVAL_ASSISTANT_BASE_URL",
            "http://127.0.0.1:8081",
        ),
    )
    assistant_run.add_argument("--concurrency", type=int, default=5)
    assistant_run.add_argument(
        "--timeout-seconds",
        type=float,
        default=120.0,
    )

    split = subparsers.add_parser(
        "split-dataset",
        help="按样本 split 字段导出 calibration/holdout 和 manifest",
    )
    split.add_argument("--dataset", type=Path, required=True)
    split.add_argument("--output-dir", type=Path, required=True)

    recalculate = subparsers.add_parser(
        "recalculate",
        help="用更新后的标签对已保存在线结果离线重算",
    )
    recalculate.add_argument("--report", type=Path, required=True)
    recalculate.add_argument("--dataset", type=Path, required=True)
    recalculate.add_argument("--label", required=True)
    recalculate.add_argument(
        "--output-dir",
        type=Path,
        default=Path("eval/reports"),
    )

    threshold = subparsers.add_parser(
        "threshold-scan",
        help="对 shadow-mode 检索报告离线扫描 reranker 阈值",
    )
    threshold.add_argument("--report", type=Path, required=True)
    threshold.add_argument("--output-dir", type=Path)
    threshold.add_argument(
        "--threshold",
        type=float,
        action="append",
        help="显式阈值，可重复传入；提供后忽略 start/stop/step",
    )
    threshold.add_argument("--start", type=float, default=0.1)
    threshold.add_argument("--stop", type=float, default=0.9)
    threshold.add_argument("--step", type=float, default=0.05)
    threshold.add_argument(
        "--max-false-accept-rate",
        type=float,
        default=0.1,
    )
    threshold.add_argument(
        "--max-false-reject-rate",
        type=float,
        default=0.15,
    )
    threshold.add_argument(
        "--min-gold-survival-rate",
        type=float,
        default=0.8,
    )

    compare = subparsers.add_parser(
        "compare",
        help="对比同一数据集的 baseline 与 experiment 报告",
    )
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--experiment", type=Path, required=True)
    compare.add_argument(
        "--output-dir",
        type=Path,
        default=Path("eval/reports/comparisons"),
    )

    stability = subparsers.add_parser(
        "stability",
        help="聚合同一冻结数据集的多次运行结果",
    )
    stability.add_argument(
        "--report",
        type=Path,
        action="append",
        required=True,
        help="run.json 路径，至少重复两次",
    )
    stability.add_argument(
        "--output-dir",
        type=Path,
        default=Path("eval/reports/stability"),
    )
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    if args.top_k < 1:
        raise ValueError("--top-k 必须大于 0")
    if args.candidate_k < args.top_k:
        raise ValueError("--candidate-k 不能小于 --top-k")
    if args.concurrency < 1 or args.concurrency > 20:
        raise ValueError("--concurrency 必须在 1 到 20 之间")
    if args.timeout_seconds <= 0:
        raise ValueError("--timeout-seconds 必须大于 0")


async def _run(args: argparse.Namespace) -> Path:
    _validate_args(args)
    cases = load_dataset(args.dataset)
    config = RunConfig(
        label=args.label,
        base_url=args.base_url,
        dataset=str(args.dataset),
        mode=args.mode,
        top_k=args.top_k,
        candidate_k=args.candidate_k,
        concurrency=args.concurrency,
        timeout_seconds=args.timeout_seconds,
        git_commit=resolve_git_commit(),
    )
    async with RagApiClient(
        base_url=args.base_url,
        api_key=os.getenv("EVAL_API_KEY", ""),
        timeout_seconds=args.timeout_seconds,
    ) as client:
        report = await run_evaluation(
            client=client,
            cases=cases,
            config=config,
        )
    return write_report(report, args.output_dir)


async def _run_assistant(args: argparse.Namespace) -> Path:
    if args.concurrency < 1 or args.concurrency > 20:
        raise ValueError("--concurrency 必须在 1 到 20 之间")
    if args.timeout_seconds <= 0:
        raise ValueError("--timeout-seconds 必须大于 0")
    cases = load_assistant_dataset(args.dataset)
    config = AssistantRunConfig(
        label=args.label,
        base_url=args.base_url,
        dataset=str(args.dataset),
        concurrency=args.concurrency,
        timeout_seconds=args.timeout_seconds,
        git_commit=resolve_git_commit(),
    )
    async with AssistantApiClient(
        base_url=args.base_url,
        api_key=os.getenv("EVAL_ASSISTANT_API_KEY", ""),
        timeout_seconds=args.timeout_seconds,
    ) as client:
        report = await run_assistant_evaluation(
            client=client,
            cases=cases,
            config=config,
        )
    return write_assistant_report(report, args.output_dir)


def _threshold_values(args: argparse.Namespace) -> list[float]:
    if args.threshold:
        values = args.threshold
    else:
        if any(
            not math.isfinite(value)
            for value in (args.start, args.stop, args.step)
        ):
            raise ValueError("--start/--stop/--step 必须是有限数值")
        if args.step <= 0:
            raise ValueError("--step 必须大于 0")
        if args.start > args.stop:
            raise ValueError("--start 不能大于 --stop")
        current = Decimal(str(args.start))
        stop = Decimal(str(args.stop))
        step = Decimal(str(args.step))
        values = []
        while current <= stop:
            values.append(float(current))
            current += step
    if any(
        not math.isfinite(value) or value < 0 or value > 1
        for value in values
    ):
        raise ValueError("threshold 必须位于 0 到 1")
    return values


def _run_threshold_scan(
    args: argparse.Namespace,
) -> tuple[Path, Path]:
    for name in (
        "max_false_accept_rate",
        "max_false_reject_rate",
        "min_gold_survival_rate",
    ):
        value = getattr(args, name)
        if not math.isfinite(value) or value < 0 or value > 1:
            raise ValueError(f"--{name.replace('_', '-')} 必须位于 0 到 1")
    report = load_run_report(args.report)
    scan = scan_thresholds(
        report,
        source_report=str(args.report),
        thresholds=_threshold_values(args),
        max_false_accept_rate=args.max_false_accept_rate,
        max_false_reject_rate=args.max_false_reject_rate,
        min_gold_survival_rate=args.min_gold_survival_rate,
    )
    return write_threshold_report(
        scan,
        args.output_dir or args.report.parent,
    )


def _run_compare(args: argparse.Namespace) -> Path:
    baseline = load_run_report(args.baseline)
    experiment = load_run_report(args.experiment)
    comparison = compare_reports(
        baseline,
        experiment,
        baseline_report=str(args.baseline),
        experiment_report=str(args.experiment),
    )
    return write_comparison_report(comparison, args.output_dir)


def _run_recalculate(args: argparse.Namespace) -> Path:
    report = load_run_report(args.report)
    cases = load_dataset(args.dataset)
    recalculated = recalculate_report(
        report,
        cases=cases,
        dataset=str(args.dataset),
        label=args.label,
    )
    return write_report(recalculated, args.output_dir)


def _run_stability(args: argparse.Namespace) -> tuple[Path, Path]:
    reports = [load_run_report(path) for path in args.report]
    stability = analyze_stability(
        reports,
        source_reports=[str(path) for path in args.report],
    )
    return write_stability_report(stability, args.output_dir)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "run":
            output = {
                "report_dir": str(asyncio.run(_run(args))),
            }
        elif args.command == "assistant-run":
            output = {
                "report_dir": str(asyncio.run(_run_assistant(args))),
            }
        elif args.command == "split-dataset":
            output = split_dataset(args.dataset, args.output_dir)
        elif args.command == "recalculate":
            output = {
                "report_dir": str(_run_recalculate(args)),
            }
        elif args.command == "threshold-scan":
            json_path, markdown_path = _run_threshold_scan(args)
            output = {
                "json": str(json_path),
                "markdown": str(markdown_path),
            }
        elif args.command == "compare":
            output = {
                "report_dir": str(_run_compare(args)),
            }
        elif args.command == "stability":
            json_path, markdown_path = _run_stability(args)
            output = {
                "json": str(json_path),
                "markdown": str(markdown_path),
            }
        else:
            raise ValueError(f"未知命令：{args.command}")
    except (AnalysisError, DatasetError, OSError, ValueError) as exc:
        print(f"评估失败：{exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {"status": "ok", **output},
            ensure_ascii=False,
        )
    )
    return 0
