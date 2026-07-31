from __future__ import annotations

import asyncio
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .client import RagApiClient
from .metrics import calculate_metrics
from .schemas import (
    CaseResult,
    EvalCase,
    EvalMode,
    RunConfig,
    RunReport,
)


def resolve_git_commit(workdir: Path | None = None) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=workdir,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


async def _evaluate_case(
    client: RagApiClient,
    case: EvalCase,
    *,
    mode: EvalMode,
    top_k: int,
    candidate_k: int,
    capacity: asyncio.Semaphore,
) -> CaseResult:
    async with capacity:
        retrieval = None
        chat = None
        if mode in {"retrieve", "all"}:
            retrieval = await client.retrieve(
                case,
                top_k=top_k,
                candidate_k=candidate_k,
            )
        if mode in {"chat", "all"}:
            chat = await client.chat(
                case,
                top_k=top_k,
                candidate_k=candidate_k,
            )
        return CaseResult(
            case=case,
            retrieval=retrieval,
            chat=chat,
        )


async def run_evaluation(
    *,
    client: RagApiClient,
    cases: list[EvalCase],
    config: RunConfig,
) -> RunReport:
    service: dict[str, Any]
    try:
        service = await client.health()
    except Exception as exc:
        service = {
            "status": "unknown",
            "error": str(exc)[:500],
        }

    capacity = asyncio.Semaphore(config.concurrency)
    started_at = time.perf_counter()
    results = await asyncio.gather(
        *(
            _evaluate_case(
                client,
                case,
                mode=config.mode,
                top_k=config.top_k,
                candidate_k=config.candidate_k,
                capacity=capacity,
            )
            for case in cases
        )
    )
    wall_elapsed_ms = round(
        (time.perf_counter() - started_at) * 1000,
        3,
    )
    summary = calculate_metrics(results, top_k=config.top_k)
    summary["efficiency"]["wall_elapsed_ms"] = wall_elapsed_ms
    summary["efficiency"]["throughput_requests_per_second"] = (
        round(len(cases) / (wall_elapsed_ms / 1000), 4)
        if wall_elapsed_ms > 0
        else None
    )
    return RunReport(
        generated_at=datetime.now(UTC).isoformat(),
        config=config,
        service=service,
        summary=summary,
        results=results,
    )
