from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any
from uuid import UUID, uuid4

from langgraph.errors import GraphRecursionError
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from ..domain.agent_actions import (
    AgentMessageInput,
    AgentResumeInput,
    TrackingResume,
)
from ..domain.agent_errors import AgentOperationError
from ..domain.failures import AgentFailure, FailureCategory
from ..domain.intents import Intent
from ..domain.tooling import ToolDescriptor
from .tracing import (
    WorkflowTraceSink,
    build_agent_workflow_trace,
)


class AgentWorkflowRuntime:
    def __init__(
        self,
        graph: CompiledStateGraph,
        *,
        recursion_limit: int = 8,
    ) -> None:
        if recursion_limit < 1:
            raise ValueError("recursion_limit 必须大于 0")
        self._graph = graph
        self._recursion_limit = recursion_limit

    @property
    def graph(self) -> CompiledStateGraph:
        return self._graph

    def config(self, thread_id: str) -> dict[str, Any]:
        normalized = thread_id.strip()
        if not normalized:
            raise ValueError("thread_id 不能为空")
        if len(normalized) > 255:
            raise ValueError("thread_id 不能超过 255 个字符")
        return {
            "configurable": {"thread_id": normalized},
            "recursion_limit": self._recursion_limit,
        }

    async def start(
        self,
        *,
        thread_id: str,
        message: str,
    ) -> Mapping[str, Any]:
        payload = AgentMessageInput(message=message)
        result = await self._graph.ainvoke(
            payload.model_dump(mode="json"),
            config=self.config(thread_id),
        )
        return result

    async def resume_tracking(
        self,
        *,
        thread_id: str,
        mail_no: str,
    ) -> Mapping[str, Any]:
        payload = TrackingResume(mail_no=mail_no)
        result = await self._graph.ainvoke(
            Command(resume=payload.model_dump(mode="json")),
            config=self.config(thread_id),
        )
        return result

    async def stream_events(
        self,
        *,
        thread_id: str,
        message: str,
    ) -> AsyncIterator[Mapping[str, Any]]:
        payload = AgentMessageInput(message=message)
        events = self._graph.astream_events(
            payload.model_dump(mode="json"),
            config=self.config(thread_id),
            version="v2",
        )
        async for event in events:
            yield event


class StatefulAgentRuntime:
    def __init__(
        self,
        graph: CompiledStateGraph,
        *,
        recursion_limit: int = 24,
        max_steps: int = 8,
        max_tool_calls: int = 1,
        max_retries: int = 1,
        request_timeout_seconds: float = 30,
        capability_descriptors: Mapping[Intent, ToolDescriptor] | None = None,
        clock: Callable[[], datetime] | None = None,
        workflow_trace_sink: WorkflowTraceSink | None = None,
    ) -> None:
        for name, value in {
            "recursion_limit": recursion_limit,
            "max_steps": max_steps,
            "max_tool_calls": max_tool_calls,
            "request_timeout_seconds": request_timeout_seconds,
        }.items():
            if value < 1:
                raise ValueError(f"{name} 必须大于 0")
        if max_retries < 0:
            raise ValueError("max_retries 不能小于 0")
        self._graph = graph
        self._recursion_limit = recursion_limit
        self._max_steps = max_steps
        self._max_tool_calls = max_tool_calls
        self._max_retries = max_retries
        self._request_timeout_seconds = request_timeout_seconds
        self._capability_descriptors = MappingProxyType(
            dict(capability_descriptors or {})
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._workflow_trace_sink = workflow_trace_sink

    @property
    def graph(self) -> CompiledStateGraph:
        return self._graph

    @property
    def capability_descriptors(self) -> Mapping[Intent, ToolDescriptor]:
        return self._capability_descriptors

    def config(self, thread_id: str) -> dict[str, Any]:
        normalized = thread_id.strip()
        if not normalized:
            raise ValueError("thread_id 不能为空")
        if len(normalized) > 255:
            raise ValueError("thread_id 不能超过 255 个字符")
        return {
            "configurable": {"thread_id": normalized},
            "recursion_limit": self._recursion_limit,
        }

    async def start(
        self,
        *,
        thread_id: str,
        message: str,
        explicit_intent: Intent | None = None,
        turn_id: UUID | None = None,
    ) -> Mapping[str, Any]:
        payload = AgentMessageInput(message=message)
        normalized_thread_id = self.config(thread_id)["configurable"][
            "thread_id"
        ]
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("clock 必须返回包含时区的 datetime")
        result = await self._invoke(
            {
                "conversation_id": normalized_thread_id,
                "turn_id": str(turn_id or uuid4()),
                "message": payload.message,
                "explicit_intent": (
                    explicit_intent.value
                    if explicit_intent is not None
                    else None
                ),
                "deadline_at": (
                    now + timedelta(seconds=self._request_timeout_seconds)
                ).isoformat(),
                "max_steps": self._max_steps,
                "max_tool_calls": self._max_tool_calls,
                "max_retries": self._max_retries,
            },
            config=self.config(thread_id),
        )
        return result

    async def resume_tracking(
        self,
        *,
        thread_id: str,
        mail_no: str,
        turn_id: UUID | None = None,
    ) -> Mapping[str, Any]:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("clock 必须返回包含时区的 datetime")
        payload = TrackingResume(
            mail_no=mail_no,
            turn_id=turn_id or uuid4(),
            deadline_at=(
                now + timedelta(seconds=self._request_timeout_seconds)
            ),
        )
        result = await self._invoke(
            Command(resume=payload.model_dump(mode="json")),
            config=self.config(thread_id),
        )
        return result

    async def resume(
        self,
        *,
        thread_id: str,
        message: str | None = None,
        selected_intent: Intent | None = None,
        confirm_overwrite: bool = False,
        turn_id: UUID | None = None,
    ) -> Mapping[str, Any]:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("clock 必须返回包含时区的 datetime")
        payload = AgentResumeInput(
            message=message,
            selected_intent=selected_intent,
            confirm_overwrite=confirm_overwrite,
            turn_id=turn_id or uuid4(),
            deadline_at=(
                now + timedelta(seconds=self._request_timeout_seconds)
            ),
        )
        return await self._invoke(
            Command(resume=payload.model_dump(mode="json")),
            config=self.config(thread_id),
        )

    async def _invoke(
        self,
        payload: object,
        *,
        config: dict[str, Any],
    ) -> Mapping[str, Any]:
        before_state: Mapping[str, Any] | None = None
        checkpoint_before = False
        if self._workflow_trace_sink is not None:
            before_state, checkpoint_before, _ = (
                await self._read_trace_checkpoint(config)
            )
        resumed = isinstance(payload, Command)
        try:
            result = await self._graph.ainvoke(payload, config=config)
        except GraphRecursionError as error:
            await self._record_workflow_trace(
                config=config,
                before_state=before_state,
                checkpoint_before=checkpoint_before,
                resumed=resumed,
                outcome_override="error",
                failure_category=(
                    FailureCategory.LOOP_BUDGET_EXCEEDED.value
                ),
                failure_code="graph_recursion_limit_exceeded",
            )
            raise AgentOperationError(
                AgentFailure(
                    category=FailureCategory.LOOP_BUDGET_EXCEEDED,
                    code="graph_recursion_limit_exceeded",
                    message="Workflow 超过允许的图执行步数",
                )
            ) from error
        except AgentOperationError as error:
            await self._record_workflow_trace(
                config=config,
                before_state=before_state,
                checkpoint_before=checkpoint_before,
                resumed=resumed,
                outcome_override="error",
                failure_category=error.failure.category.value,
                failure_code=error.failure.code,
            )
            raise
        except Exception:
            await self._record_workflow_trace(
                config=config,
                before_state=before_state,
                checkpoint_before=checkpoint_before,
                resumed=resumed,
                outcome_override="error",
                failure_category=FailureCategory.INTERNAL_ERROR.value,
                failure_code="workflow_unhandled_error",
            )
            raise
        await self._record_workflow_trace(
            config=config,
            before_state=before_state,
            checkpoint_before=checkpoint_before,
            resumed=resumed,
            fallback_output=result,
        )
        return result

    async def _record_workflow_trace(
        self,
        *,
        config: dict[str, Any],
        before_state: Mapping[str, Any] | None,
        checkpoint_before: bool,
        resumed: bool,
        fallback_output: Mapping[str, Any] | None = None,
        outcome_override: str | None = None,
        failure_category: str | None = None,
        failure_code: str | None = None,
    ) -> None:
        sink = self._workflow_trace_sink
        if sink is None:
            return
        try:
            after_state, checkpoint_after, after_next = (
                await self._read_trace_checkpoint(config)
            )
            trace = build_agent_workflow_trace(
                before_state=before_state,
                after_state=after_state,
                after_next=after_next,
                resumed=resumed,
                checkpoint_before=checkpoint_before,
                checkpoint_after=checkpoint_after,
                fallback_output=fallback_output,
                outcome_override=outcome_override,
                failure_category=failure_category,
                failure_code=failure_code,
            )
            sink(trace)
        except Exception:
            # Telemetry must never change the workflow outcome. The sink is
            # intentionally synchronous and receives no prompts or state.
            return

    async def _read_trace_checkpoint(
        self,
        config: dict[str, Any],
    ) -> tuple[Mapping[str, Any] | None, bool, tuple[str, ...]]:
        try:
            snapshot = await self._graph.aget_state(config)
        except Exception:
            return None, False, ()
        values = snapshot.values
        if not isinstance(values, Mapping):
            return None, False, tuple(snapshot.next)
        return dict(values), bool(values), tuple(snapshot.next)

    async def stream_events(
        self,
        *,
        thread_id: str,
        message: str,
        explicit_intent: Intent | None = None,
    ) -> AsyncIterator[Mapping[str, Any]]:
        payload = AgentMessageInput(message=message)
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("clock 必须返回包含时区的 datetime")
        events = self._graph.astream_events(
            {
                "conversation_id": self.config(thread_id)["configurable"][
                    "thread_id"
                ],
                "turn_id": str(uuid4()),
                "message": payload.message,
                "explicit_intent": (
                    explicit_intent.value
                    if explicit_intent is not None
                    else None
                ),
                "deadline_at": (
                    now + timedelta(seconds=self._request_timeout_seconds)
                ).isoformat(),
                "max_steps": self._max_steps,
                "max_tool_calls": self._max_tool_calls,
                "max_retries": self._max_retries,
            },
            config=self.config(thread_id),
            version="v2",
        )
        async for event in events:
            yield event


# Phase 1 compatibility alias. New composition code uses the capability-neutral
# name because the same runtime now hosts multiple query tools.
TrackingAgentRuntime = StatefulAgentRuntime
