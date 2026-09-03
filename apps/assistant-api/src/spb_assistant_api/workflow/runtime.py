from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from ..domain.agent_actions import AgentMessageInput, TrackingResume
from ..domain.intents import Intent


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


class TrackingAgentRuntime:
    def __init__(
        self,
        graph: CompiledStateGraph,
        *,
        recursion_limit: int = 24,
        max_steps: int = 8,
        max_tool_calls: int = 1,
        max_retries: int = 1,
        request_timeout_seconds: float = 30,
        clock: Callable[[], datetime] | None = None,
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
        self._clock = clock or (lambda: datetime.now(UTC))

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
        result = await self._graph.ainvoke(
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
