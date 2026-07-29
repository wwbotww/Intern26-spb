from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
from dataclasses import dataclass
from time import perf_counter

import httpx


@dataclass(frozen=True)
class Result:
    status_code: int
    elapsed_ms: float
    error: str = ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SPB RAG API 轻量并发压测（默认不调用 DeepSeek）"
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8080",
    )
    parser.add_argument(
        "--endpoint",
        choices=["retrieve", "chat"],
        default="retrieve",
    )
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument(
        "--question",
        default="快递业务经营许可需要符合哪些条件？",
    )
    return parser


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(
        len(ordered) - 1,
        max(0, round((len(ordered) - 1) * quantile)),
    )
    return ordered[index]


async def run(args: argparse.Namespace) -> dict:
    if args.requests < 1 or args.concurrency < 1:
        raise ValueError("requests 和 concurrency 必须为正整数")
    if args.concurrency > args.requests:
        raise ValueError("concurrency 不能大于 requests")

    api_key = os.getenv("SPB_RAG_API_KEY", "").strip()
    headers = (
        {"Authorization": f"Bearer {api_key}"}
        if api_key
        else {}
    )
    semaphore = asyncio.Semaphore(args.concurrency)
    if args.endpoint == "retrieve":
        path = "/v1/retrieve"
        payload = {"query": args.question, "top_k": 5}
    else:
        path = "/v1/chat"
        payload = {
            "question": args.question,
            "stream": False,
            "top_k": 5,
        }

    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/"),
        headers=headers,
        timeout=args.timeout,
    ) as client:
        async def one() -> Result:
            async with semaphore:
                started = perf_counter()
                try:
                    response = await client.post(path, json=payload)
                    return Result(
                        status_code=response.status_code,
                        elapsed_ms=(perf_counter() - started) * 1000,
                    )
                except httpx.HTTPError as exc:
                    return Result(
                        status_code=0,
                        elapsed_ms=(perf_counter() - started) * 1000,
                        error=type(exc).__name__,
                    )

        started = perf_counter()
        results = await asyncio.gather(
            *(one() for _ in range(args.requests))
        )
        wall_seconds = perf_counter() - started

    latencies = [item.elapsed_ms for item in results]
    successful = [
        item for item in results if 200 <= item.status_code < 300
    ]
    status_counts: dict[str, int] = {}
    for item in results:
        key = str(item.status_code) if item.status_code else item.error
        status_counts[key] = status_counts.get(key, 0) + 1
    return {
        "endpoint": args.endpoint,
        "requests": args.requests,
        "concurrency": args.concurrency,
        "successful": len(successful),
        "failed": len(results) - len(successful),
        "wall_seconds": round(wall_seconds, 3),
        "throughput_rps": round(len(results) / wall_seconds, 3),
        "latency_ms": {
            "min": round(min(latencies), 3),
            "mean": round(statistics.fmean(latencies), 3),
            "p50": round(percentile(latencies, 0.50), 3),
            "p95": round(percentile(latencies, 0.95), 3),
            "max": round(max(latencies), 3),
        },
        "status_counts": status_counts,
    }


def main() -> int:
    args = build_parser().parse_args()
    result = asyncio.run(run(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
