from __future__ import annotations

import asyncio
import hashlib
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .client import AgentApiClient, AssistantApiClient, RagApiClient
from .metrics import (
    calculate_agent_metrics,
    calculate_assistant_metrics,
    calculate_metrics,
)
from .schemas import (
    AgentCaseResult,
    AgentEvalCase,
    AgentRunConfig,
    AgentRunReport,
    AgentTurnResult,
    AssistantCaseResult,
    AssistantEvalCase,
    AssistantRunConfig,
    AssistantRunReport,
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
    commit = result.stdout.strip() or "unknown"
    if commit == "unknown":
        return commit
    try:
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=workdir,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return commit
    return f"{commit}-dirty" if dirty.stdout.strip() else commit


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


async def _evaluate_assistant_case(
    client: AssistantApiClient,
    case: AssistantEvalCase,
    *,
    capacity: asyncio.Semaphore,
) -> AssistantCaseResult:
    async with capacity:
        return AssistantCaseResult(
            case=case,
            chat=await client.chat(case),
        )


async def run_assistant_evaluation(
    *,
    client: AssistantApiClient,
    cases: list[AssistantEvalCase],
    config: AssistantRunConfig,
) -> AssistantRunReport:
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
            _evaluate_assistant_case(
                client,
                case,
                capacity=capacity,
            )
            for case in cases
        )
    )
    wall_elapsed_ms = round(
        (time.perf_counter() - started_at) * 1000,
        3,
    )
    summary = calculate_assistant_metrics(results)
    summary["efficiency"]["wall_elapsed_ms"] = wall_elapsed_ms
    summary["efficiency"]["throughput_requests_per_second"] = (
        round(len(cases) / (wall_elapsed_ms / 1000), 4)
        if wall_elapsed_ms > 0
        else None
    )
    return AssistantRunReport(
        generated_at=datetime.now(UTC).isoformat(),
        config=config,
        service=service,
        summary=summary,
        results=results,
    )


async def _evaluate_agent_case(
    client: AgentApiClient,
    case: AgentEvalCase,
    *,
    capacity: asyncio.Semaphore,
    run_token: str,
) -> AgentCaseResult:
    async with capacity:
        conversation_id: str | None = None
        turn_results: list[AgentTurnResult] = []
        case_token = hashlib.sha256(case.id.encode("utf-8")).hexdigest()[:16]
        for turn_index, turn in enumerate(case.turns, start=1):
            observation = await client.send_message(
                turn,
                conversation_id=conversation_id,
                idempotency_key=(
                    f"agent-eval-{run_token}-{case_token}-{turn_index}"
                ),
            )
            turn_results.append(
                AgentTurnResult(
                    turn_index=turn_index,
                    expected=turn,
                    observation=observation,
                )
            )
            if observation.status == "error":
                break
            conversation_id = observation.conversation_id
        return AgentCaseResult(case=case, turns=turn_results)


async def run_agent_evaluation(
    *,
    client: AgentApiClient,
    cases: list[AgentEvalCase],
    config: AgentRunConfig,
) -> AgentRunReport:
    """Evaluate independent scenarios; turns inside one scenario stay ordered."""
    service: dict[str, Any] = {}
    try:
        readiness = await client.readiness()
        service["readiness"] = readiness
        service["version"] = readiness.get("version", "unknown")
    except Exception as exc:
        service["readiness"] = {
            "status": "unknown",
            "error": str(exc)[:500],
        }
        service["version"] = "unknown"
    try:
        service["capabilities"] = await client.capabilities()
    except Exception as exc:
        service["capabilities"] = []
        service["capabilities_error"] = str(exc)[:500]

    capacity = asyncio.Semaphore(config.concurrency)
    run_token = uuid4().hex[:12]
    started_at = time.perf_counter()
    results = await asyncio.gather(
        *(
            _evaluate_agent_case(
                client,
                case,
                capacity=capacity,
                run_token=run_token,
            )
            for case in cases
        )
    )
    wall_elapsed_ms = round(
        (time.perf_counter() - started_at) * 1000,
        3,
    )
    summary = calculate_agent_metrics(
        results,
        thresholds=config.thresholds,
    )
    expected_turns = sum(len(case.turns) for case in cases)
    summary["efficiency"]["wall_elapsed_ms"] = wall_elapsed_ms
    summary["efficiency"]["throughput_turns_per_second"] = (
        round(expected_turns / (wall_elapsed_ms / 1000), 4)
        if wall_elapsed_ms > 0
        else None
    )
    return AgentRunReport(
        generated_at=datetime.now(UTC).isoformat(),
        config=config,
        service=service,
        summary=summary,
        results=results,
    )
