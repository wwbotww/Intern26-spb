from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx

from spb_assistant_api.adapters.fake_tracking import FakeTrackingGateway
from spb_assistant_api.api.agent_contracts import AgentApiDependencies
from spb_assistant_api.api.app import create_app
from spb_assistant_api.domain.agent_errors import AgentOperationError
from spb_assistant_api.domain.failures import AgentFailure, FailureCategory
from spb_assistant_api.domain.results import TrackingData
from spb_assistant_api.settings import AssistantSettings
from spb_assistant_api.workflow.composition import create_persistent_agent


NOW = datetime(2026, 9, 4, 9, tzinfo=UTC)
MAIL_NO = "1234567890123"
AUTH = {"Authorization": "Bearer phase4b-client"}


def _settings() -> AssistantSettings:
    return AssistantSettings(
        auth_enabled=True,
        api_keys="phase4b-client",
        rate_limit_enabled=False,
        metrics_enabled=False,
    )


def _events(response: httpx.Response) -> list[tuple[str, dict[str, Any]]]:
    parsed: list[tuple[str, dict[str, Any]]] = []
    for block in response.text.strip().split("\n\n"):
        lines = block.splitlines()
        event = next(line[7:] for line in lines if line.startswith("event: "))
        data = next(line[6:] for line in lines if line.startswith("data: "))
        parsed.append((event, json.loads(data)))
    return parsed


def test_sse_projects_stable_public_events_without_graph_internals(
    tmp_path: Path,
) -> None:
    async def scenario() -> httpx.Response:
        gateway = FakeTrackingGateway(
            {
                MAIL_NO: TrackingData(
                    mail_no=MAIL_NO,
                    current_status="运输中",
                    queried_at=NOW,
                )
            }
        )
        async with create_persistent_agent(
            database_path=tmp_path / "stream-complete.db",
            tracking_gateway=gateway,
            clock=lambda: NOW,
        ) as components:
            app = create_app(
                settings=_settings(),
                agent_api=AgentApiDependencies(
                    service=components.service,
                    capabilities=components.runtime.capability_descriptors,
                ),
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://assistant.test",
            ) as client:
                return await client.post(
                    "/v2/agent/messages",
                    headers={
                        **AUTH,
                        "Idempotency-Key": "stream-complete",
                        "X-Request-ID": "stream-request",
                    },
                    json={
                        "message": f"查询邮件 {MAIL_NO}",
                        "explicit_intent": "tracking",
                        "stream": True,
                    },
                )

    response = asyncio.run(scenario())
    events = _events(response)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert response.headers["x-accel-buffering"] == "no"
    assert [name for name, _ in events] == [
        "status",
        "state",
        "result",
        "delta",
        "done",
    ]
    assert all(payload["schema_version"] == "1" for _, payload in events)
    assert events[1][1]["phase"] == "completed"
    assert events[2][1]["result"]["type"] == "tracking"
    assert events[-1][1]["response"]["request_id"] == "stream-request"
    serialized = json.dumps(events, ensure_ascii=False)
    assert "langgraph" not in serialized.lower()
    assert "node" not in serialized.lower()
    assert "messages" not in serialized.lower()


def test_json_and_sse_replay_share_transport_neutral_idempotency(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[httpx.Response, httpx.Response]:
        async with create_persistent_agent(
            database_path=tmp_path / "transport-replay.db",
            tracking_gateway=FakeTrackingGateway(),
            clock=lambda: NOW,
        ) as components:
            app = create_app(
                settings=_settings(),
                agent_api=AgentApiDependencies(
                    service=components.service,
                    capabilities=components.runtime.capability_descriptors,
                ),
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://assistant.test",
            ) as client:
                headers = {
                    **AUTH,
                    "Idempotency-Key": "transport-neutral",
                }
                json_response = await client.post(
                    "/v2/agent/messages",
                    headers=headers,
                    json={"message": "帮我查邮件"},
                )
                stream_response = await client.post(
                    "/v2/agent/messages",
                    headers=headers,
                    json={"message": "帮我查邮件", "stream": True},
                )
                return json_response, stream_response

    json_response, stream_response = asyncio.run(scenario())
    stream_done = _events(stream_response)[-1][1]["response"]

    assert json_response.status_code == 200
    assert json_response.json()["phase"] == "waiting_user"
    assert stream_done["conversation_id"] == json_response.json()[
        "conversation_id"
    ]
    assert stream_done["turn_id"] == json_response.json()["turn_id"]


class _FailingService:
    async def send_message(self, **_: Any) -> dict[str, Any]:
        raise AgentOperationError(
            AgentFailure(
                category=FailureCategory.UPSTREAM_RATE_LIMITED,
                code="tracking_rate_limited",
                message="sensitive upstream message",
                retryable=True,
                retry_after_seconds=2.2,
            )
        )

    async def delete_conversation(self, **_: Any) -> None:
        return None


def test_failure_after_stream_start_is_a_sanitized_terminal_error_event() -> None:
    conversation_id = uuid4()

    async def scenario() -> httpx.Response:
        app = create_app(
            settings=_settings(),
            agent_api=AgentApiDependencies(
                service=_FailingService(),
                capabilities={},
            ),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://assistant.test",
        ) as client:
            return await client.post(
                "/v2/agent/messages",
                headers={
                    **AUTH,
                    "Idempotency-Key": "stream-error",
                    "X-Request-ID": "error-request",
                },
                json={
                    "conversation_id": str(conversation_id),
                    "message": "继续查询",
                    "stream": True,
                },
            )

    response = asyncio.run(scenario())
    events = _events(response)

    assert response.status_code == 200
    assert [name for name, _ in events] == ["status", "error"]
    assert events[-1][1] == {
        "schema_version": "1",
        "request_id": "error-request",
        "code": "tracking_rate_limited",
        "message": "查询依赖暂时不可用，请稍后重试",
        "http_status": 503,
        "category": "upstream_rate_limited",
        "retryable": True,
        "retry_after_seconds": 2.2,
    }
    assert "sensitive upstream message" not in response.text


def test_invalid_stream_request_fails_before_sse_headers_are_sent() -> None:
    async def scenario() -> httpx.Response:
        app = create_app(
            settings=_settings(),
            agent_api=AgentApiDependencies(
                service=_FailingService(),
                capabilities={},
            ),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://assistant.test",
        ) as client:
            return await client.post(
                "/v2/agent/messages",
                headers={
                    **AUTH,
                    "Idempotency-Key": "invalid-stream",
                },
                json={"stream": True},
            )

    response = asyncio.run(scenario())

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["detail"]["code"] == "invalid_agent_request"
