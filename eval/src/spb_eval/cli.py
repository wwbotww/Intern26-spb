from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from .client import RagApiClient
from .dataset import DatasetError, load_dataset
from .reporting import write_report
from .runner import resolve_git_commit, run_evaluation
from .schemas import RunConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spb-eval",
        description="国家邮政局政策 RAG API 黑盒评估工具",
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        output_dir = asyncio.run(_run(args))
    except (DatasetError, OSError, ValueError) as exc:
        print(f"评估失败：{exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {"status": "ok", "report_dir": str(output_dir)},
            ensure_ascii=False,
        )
    )
    return 0
