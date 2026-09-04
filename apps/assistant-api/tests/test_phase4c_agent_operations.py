from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from spb_assistant_api.adapters.fake_tracking import FakeTrackingGateway
from spb_assistant_api.api.agent_contracts import AgentApiDependencies
from spb_assistant_api.api.app import create_app
from spb_assistant_api.domain.agent_errors import AgentOperationError
from spb_assistant_api.domain.failures import AgentFailure, FailureCategory
from spb_assistant_api.domain.results import TrackingData
from spb_assistant_api.observability.metrics import ServiceMetrics
from spb_assistant_api.services.agent_operations import AgentJanitorScheduler
from spb_assistant_api.settings import AssistantSettings
from spb_assistant_api.tools.tracking import TRACKING_DESCRIPTOR
from spb_assistant_api.workflow.composition import create_persistent_agent
from spb_assistant_api.workflow.conversation_service import CleanupResult


NOW = datetime(2026, 9, 4, 10, tzinfo=UTC)
MAIL_NO = "1234567890123"


def _settings() -> AssistantSettings:
    return AssistantSettings(
        auth_enabled=True,
        api_keys="phase4c-client",
        rate_limit_enabled=True,
        rate_limit_requests=10,
        metrics_enabled=True,
    )


def _dependency_factory(database_path: Path):
    @asynccontextmanager
    async def dependencies() -> AsyncIterator[AgentApiDependencies]:
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
            database_path=database_path,
            tracking_gateway=gateway,
            clock=lambda: NOW,
        ) as components:
            yield AgentApiDependencies(
                service=components.service,
                capabilities=components.runtime.capability_descriptors,
                readiness_probe=components.readiness,
                janitor=components.janitor,
                janitor_interval_seconds=3600,
            )

    return dependencies


def test_v2_readiness_is_operational_and_auth_exempt(tmp_path: Path) -> None:
    app = create_app(
        settings=_settings(),
        agent_api_factory=_dependency_factory(tmp_path / "ready.db"),
    )

    with TestClient(app) as client:
        response = client.get("/v2/agent/health/ready")
        metrics = client.get("/metrics")

        assert app.state.agent_janitor_scheduler is not None

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["phase"] == 4
    assert payload["checks"] == {
        "agent_api": "ready",
        "persistence": "ready",
        "checkpoint": "ready",
        "janitor": "ready",
        "capability.policy": "disabled",
        "capability.device_price": "disabled",
        "capability.tracking": "ready",
        "capability.delivery_time": "disabled",
        "capability.postage": "disabled",
    }
    assert "assistant_agent_readiness" in metrics.text
    assert 'component="persistence"} 1.0' in metrics.text
    assert 'component="capability_tracking"} 1.0' in metrics.text
    assert app.state.agent_api is None
    assert app.state.agent_janitor_scheduler is None


class _NoopService:
    pass


def test_v2_readiness_fails_closed_without_persistence_probe() -> None:
    app = create_app(
        settings=AssistantSettings(
            auth_enabled=False,
            rate_limit_enabled=False,
            metrics_enabled=False,
        ),
        agent_api=AgentApiDependencies(
            service=_NoopService(),  # type: ignore[arg-type]
            capabilities={TRACKING_DESCRIPTOR.intent: TRACKING_DESCRIPTOR},
        ),
    )

    with TestClient(app) as client:
        response = client.get("/v2/agent/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["persistence"] == "not_ready"
    assert response.json()["checks"]["checkpoint"] == "not_ready"
    assert response.json()["checks"]["janitor"] == "disabled"


class _FailingService:
    async def send_message(self, **_: Any) -> dict[str, Any]:
        raise AgentOperationError(
            AgentFailure(
                category=FailureCategory.UPSTREAM_UNAVAILABLE,
                code="tracking_upstream_unavailable",
                message="sensitive-provider-detail",
                retryable=True,
            )
        )


def test_agent_failure_metric_uses_category_not_failure_detail(
    caplog: Any,
) -> None:
    caplog.set_level(
        logging.INFO,
        logger="spb_assistant_api.agent_trace",
    )
    app = create_app(
        settings=AssistantSettings(
            auth_enabled=False,
            rate_limit_enabled=False,
            metrics_enabled=True,
        ),
        agent_api=AgentApiDependencies(
            service=_FailingService(),  # type: ignore[arg-type]
            capabilities={TRACKING_DESCRIPTOR.intent: TRACKING_DESCRIPTOR},
        ),
    )

    with TestClient(app) as client:
        failed = client.post(
            "/v2/agent/messages",
            headers={"Idempotency-Key": "failure-metric"},
            json={
                "conversation_id": str(uuid4()),
                "message": "provider-sensitive-user-text",
                "explicit_intent": "tracking",
            },
        )
        metrics = client.get("/metrics").text

    assert failed.status_code == 503
    assert (
        'category="upstream_unavailable",transport="json"' in metrics
    )
    assert "sensitive-provider-detail" not in caplog.text
    assert "provider-sensitive-user-text" not in caplog.text


def test_agent_metrics_and_trace_use_only_bounded_redacted_fields(
    tmp_path: Path,
    caplog: Any,
) -> None:
    caplog.set_level(
        logging.INFO,
        logger="spb_assistant_api.agent_trace",
    )
    app = create_app(
        settings=_settings(),
        agent_api_factory=_dependency_factory(tmp_path / "telemetry.db"),
    )
    headers = {
        "Authorization": "Bearer phase4c-client",
        "X-Request-ID": "phase4c-trace",
    }

    with TestClient(app) as client:
        completed = client.post(
            "/v2/agent/messages",
            headers={**headers, "Idempotency-Key": "complete"},
            json={
                "message": f"查询邮件 {MAIL_NO}",
                "explicit_intent": "tracking",
            },
        )
        waiting = client.post(
            "/v2/agent/messages",
            headers={**headers, "Idempotency-Key": "waiting"},
            json={
                "message": "查询邮件",
                "explicit_intent": "tracking",
                "stream": True,
            },
        )
        metrics = client.get("/metrics").text

    assert completed.status_code == 200
    assert waiting.status_code == 200
    assert 'intent="tracking",outcome="completed",transport="json"' in metrics
    assert (
        'intent="tracking",outcome="waiting_user",transport="sse"'
        in metrics
    )
    assert 'intent="tracking",reason="collect_slots"' in metrics
    assert "assistant_agent_runs_in_flight 0.0" in metrics

    traces = [
        record
        for record in caplog.records
        if record.name == "spb_assistant_api.agent_trace"
    ]
    assert len(traces) == 2
    assert {record.outcome for record in traces} == {
        "completed",
        "waiting_user",
    }
    waiting_trace = next(
        record for record in traces if record.outcome == "waiting_user"
    )
    assert waiting_trace.required_input_names == ["mail_no"]
    assert waiting_trace.conversation_ref.startswith("sha256:")
    encoded = json.dumps(
        [record.__dict__ for record in traces],
        ensure_ascii=False,
        default=str,
    )
    assert MAIL_NO not in encoded
    assert "查询邮件" not in encoded
    assert "current_status" not in encoded


class _ScriptedJanitor:
    def __init__(self) -> None:
        self.calls = 0
        self.second_call = asyncio.Event()

    async def cleanup_expired(self) -> CleanupResult:
        self.calls += 1
        if self.calls >= 2:
            self.second_call.set()
            return CleanupResult(
                expired_conversations=0,
                deleted_idempotency_receipts=0,
                deleted_tool_receipts=0,
                failures=("must-not-be-logged",),
            )
        return CleanupResult(
            expired_conversations=1,
            deleted_idempotency_receipts=2,
            deleted_tool_receipts=3,
        )


def test_janitor_scheduler_runs_periodically_and_reports_degraded_state(
    caplog: Any,
) -> None:
    async def scenario() -> tuple[str, str, int]:
        janitor = _ScriptedJanitor()
        metrics = ServiceMetrics()
        scheduler = AgentJanitorScheduler(
            janitor=janitor,
            metrics=metrics,
            interval_seconds=0.01,
            timeout_seconds=1,
        )
        await scheduler.start()
        await asyncio.wait_for(janitor.second_call.wait(), timeout=1)
        await scheduler.close()
        return scheduler.readiness, metrics.render().decode(), janitor.calls

    caplog.set_level(logging.INFO)
    readiness, metrics, calls = asyncio.run(scenario())

    assert calls >= 2
    assert readiness == "degraded"
    assert 'outcome="success"} 1.0' in metrics
    assert 'outcome="partial"} 1.0' in metrics
    assert 'resource="conversation"} 1.0' in metrics
    assert "must-not-be-logged" not in caplog.text


def test_janitor_cleanup_is_bounded_by_configured_batch(tmp_path: Path) -> None:
    current = [NOW]

    async def scenario() -> tuple[int, int]:
        async with create_persistent_agent(
            database_path=tmp_path / "bounded-cleanup.db",
            tracking_gateway=FakeTrackingGateway(),
            conversation_ttl=timedelta(minutes=1),
            janitor_batch_size=1,
            clock=lambda: current[0],
        ) as components:
            for value in (1, 2):
                await components.service.create_conversation(
                    owner_id="phase4c-owner",
                    conversation_id=UUID(int=value),
                )
            current[0] = NOW + timedelta(minutes=2)
            first = await components.janitor.cleanup_expired()
            second = await components.janitor.cleanup_expired()
            return first.expired_conversations, second.expired_conversations

    first, second = asyncio.run(scenario())

    assert (first, second) == (1, 1)
