from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx

from spb_assistant_api.adapters.fake_shipping import (
    FakeDeliveryTimeGateway,
    FakePostageGateway,
)
from spb_assistant_api.adapters.fake_tracking import FakeTrackingGateway
from spb_assistant_api.api.agent_contracts import AgentApiDependencies
from spb_assistant_api.api.app import create_app
from spb_assistant_api.domain.commands import TrackingCommand
from spb_assistant_api.domain.results import (
    DeliveryTimeData,
    PostageData,
    TrackingData,
)
from spb_assistant_api.domain.slots import (
    RegionRef,
    RegionResolution,
    WeightValue,
)
from spb_assistant_api.settings import AssistantSettings
from spb_assistant_api.workflow.composition import create_persistent_agent


NOW = datetime(2026, 9, 4, 9, tzinfo=UTC)
MAIL_NO = "1234567890123"
AUTH_A = {"Authorization": "Bearer phase4a-client-a"}
AUTH_B = {"Authorization": "Bearer phase4a-client-b"}


class TimeoutOnceTrackingGateway:
    def __init__(self) -> None:
        self.commands: list[TrackingCommand] = []

    async def query(
        self,
        command: TrackingCommand,
    ) -> TrackingData | None:
        self.commands.append(command)
        if len(self.commands) == 1:
            await asyncio.sleep(5)
        return _tracking_data()


def _settings() -> AssistantSettings:
    return AssistantSettings(
        auth_enabled=True,
        api_keys="phase4a-client-a,phase4a-client-b",
        rate_limit_enabled=False,
        metrics_enabled=False,
    )


def _region(name: str, province_code: str, city_code: str) -> RegionRef:
    return RegionRef(
        raw_text=name,
        canonical_name=name,
        province_code=province_code,
        city_code=city_code,
        resolution=RegionResolution.RESOLVED,
    )


BEIJING = _region("北京市", "110000", "110100")
SHANGHAI = _region("上海市", "310000", "310100")


def _tracking_data() -> TrackingData:
    return TrackingData(
        mail_no=MAIL_NO,
        current_status="运输中",
        queried_at=NOW,
    )


async def _client_for(components) -> httpx.AsyncClient:
    app = create_app(
        settings=_settings(),
        agent_api=AgentApiDependencies(
            service=components.service,
            capabilities=components.runtime.capability_descriptors,
        ),
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://assistant.test",
    )


def test_capabilities_report_registered_and_unavailable_tools(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with create_persistent_agent(
            database_path=tmp_path / "capabilities.db",
            tracking_gateway=FakeTrackingGateway(),
            delivery_time_gateway=FakeDeliveryTimeGateway(),
            postage_gateway=FakePostageGateway(),
            clock=lambda: NOW,
        ) as components:
            async with await _client_for(components) as client:
                unauthorized = await client.get(
                    "/v2/agent/capabilities"
                )
                response = await client.get(
                    "/v2/agent/capabilities",
                    headers=AUTH_A,
                )

        assert unauthorized.status_code == 401
        assert response.status_code == 200
        capabilities = {
            item["intent"]: item for item in response.json()
        }
        assert set(capabilities) == {
            "policy",
            "device_price",
            "tracking",
            "delivery_time",
            "postage",
        }
        assert capabilities["tracking"]["available"] is True
        assert capabilities["delivery_time"]["available"] is True
        assert capabilities["postage"]["available"] is True
        assert capabilities["policy"]["available"] is False
        assert capabilities["device_price"]["available"] is False
        assert [
            item["name"]
            for item in capabilities["postage"]["required_inputs"]
        ] == ["origin", "destination", "weight"]

    asyncio.run(scenario())


def test_create_resume_and_replay_use_one_durable_conversation(
    tmp_path: Path,
) -> None:
    gateway = FakeTrackingGateway({MAIL_NO: _tracking_data()})

    async def scenario() -> None:
        async with create_persistent_agent(
            database_path=tmp_path / "messages.db",
            tracking_gateway=gateway,
            clock=lambda: NOW,
        ) as components:
            async with await _client_for(components) as client:
                created = await client.post(
                    "/v2/agent/messages",
                    headers={
                        **AUTH_A,
                        "Idempotency-Key": "create-tracking-1",
                        "X-Request-ID": "create-request",
                    },
                    json={"message": "帮我查一下邮件"},
                )
                replayed_create = await client.post(
                    "/v2/agent/messages",
                    headers={
                        **AUTH_A,
                        "Idempotency-Key": "create-tracking-1",
                        "X-Request-ID": "create-replay-request",
                    },
                    json={"message": "帮我查一下邮件"},
                )
                conversation_id = created.json()["conversation_id"]
                resumed = await client.post(
                    "/v2/agent/messages",
                    headers={
                        **AUTH_A,
                        "Idempotency-Key": "resume-tracking-1",
                        "X-Request-ID": "resume-request",
                    },
                    json={
                        "conversation_id": conversation_id,
                        "message": MAIL_NO,
                    },
                )
                replayed_resume = await client.post(
                    "/v2/agent/messages",
                    headers={
                        **AUTH_A,
                        "Idempotency-Key": "resume-tracking-1",
                    },
                    json={
                        "conversation_id": conversation_id,
                        "message": MAIL_NO,
                    },
                )
                new_turn = await client.post(
                    "/v2/agent/messages",
                    headers={
                        **AUTH_A,
                        "Idempotency-Key": "new-turn-tracking-1",
                    },
                    json={
                        "conversation_id": conversation_id,
                        "message": f"再查一次邮件 {MAIL_NO}",
                    },
                )

        assert created.status_code == 200
        assert created.headers["X-Request-ID"] == "create-request"
        waiting = created.json()
        assert waiting["phase"] == "waiting_user"
        assert waiting["next_action"] == "collect_slots"
        assert waiting["required_inputs"][0]["name"] == "mail_no"
        assert replayed_create.status_code == 200
        assert replayed_create.json()["conversation_id"] == conversation_id
        assert replayed_create.json()["turn_id"] == waiting["turn_id"]

        assert resumed.status_code == 200
        completed = resumed.json()
        assert completed["request_id"] == "resume-request"
        assert completed["phase"] == "completed"
        assert completed["next_action"] == "complete"
        assert completed["result"]["type"] == "tracking"
        assert completed["result"]["data"]["mail_no"] == MAIL_NO
        assert replayed_resume.status_code == 200
        assert replayed_resume.json()["turn_id"] == completed["turn_id"]
        assert new_turn.status_code == 200
        assert new_turn.json()["turn_id"] != completed["turn_id"]
        assert len(gateway.commands) == 1

    asyncio.run(scenario())


def test_creation_idempotency_rejects_a_changed_request(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with create_persistent_agent(
            database_path=tmp_path / "creation-conflict.db",
            tracking_gateway=FakeTrackingGateway(),
            clock=lambda: NOW,
        ) as components:
            async with await _client_for(components) as client:
                first = await client.post(
                    "/v2/agent/messages",
                    headers={
                        **AUTH_A,
                        "Idempotency-Key": "same-create-key",
                    },
                    json={"message": "帮我查邮件"},
                )
                conflict = await client.post(
                    "/v2/agent/messages",
                    headers={
                        **AUTH_A,
                        "Idempotency-Key": "same-create-key",
                    },
                    json={"message": "帮我查邮费"},
                )

        assert first.status_code == 200
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == (
            "idempotency_request_hash_conflict"
        )

    asyncio.run(scenario())


def test_creation_receipt_replays_the_same_conversation_after_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "creation-restart.db"

    async def scenario() -> None:
        async with create_persistent_agent(
            database_path=database,
            tracking_gateway=FakeTrackingGateway(),
            clock=lambda: NOW,
        ) as first_components:
            async with await _client_for(first_components) as client:
                first = await client.post(
                    "/v2/agent/messages",
                    headers={
                        **AUTH_A,
                        "Idempotency-Key": "restart-create-key",
                    },
                    json={"message": "帮我查邮件"},
                )

        async with create_persistent_agent(
            database_path=database,
            tracking_gateway=FakeTrackingGateway(),
            clock=lambda: NOW,
        ) as restarted_components:
            async with await _client_for(restarted_components) as client:
                replayed = await client.post(
                    "/v2/agent/messages",
                    headers={
                        **AUTH_A,
                        "Idempotency-Key": "restart-create-key",
                    },
                    json={"message": "帮我查邮件"},
                )

        assert first.status_code == 200
        assert replayed.status_code == 200
        assert replayed.json()["conversation_id"] == (
            first.json()["conversation_id"]
        )
        assert replayed.json()["turn_id"] == first.json()["turn_id"]

    asyncio.run(scenario())


def test_intent_choice_can_resume_without_repeating_the_original_message(
    tmp_path: Path,
) -> None:
    gateway = FakeTrackingGateway({MAIL_NO: _tracking_data()})

    async def scenario() -> None:
        async with create_persistent_agent(
            database_path=tmp_path / "intent-choice.db",
            tracking_gateway=gateway,
            postage_gateway=FakePostageGateway(
                PostageData(
                    origin=BEIJING,
                    destination=SHANGHAI,
                    input_weight=WeightValue(
                        value=Decimal("2"),
                        unit="kg",
                    ),
                    amount=Decimal("12.30"),
                    currency="CNY",
                    queried_at=NOW,
                )
            ),
            clock=lambda: NOW,
        ) as components:
            async with await _client_for(components) as client:
                waiting = await client.post(
                    "/v2/agent/messages",
                    headers={
                        **AUTH_A,
                        "Idempotency-Key": "multi-intent-start",
                    },
                    json={
                        "message": (
                            f"查邮件 {MAIL_NO}，同时算北京到上海 2 公斤邮费"
                        )
                    },
                )
                conversation_id = waiting.json()["conversation_id"]
                selected = await client.post(
                    "/v2/agent/messages",
                    headers={
                        **AUTH_A,
                        "Idempotency-Key": "multi-intent-select",
                    },
                    json={
                        "conversation_id": conversation_id,
                        "explicit_intent": "tracking",
                    },
                )

        assert waiting.status_code == 200
        assert waiting.json()["next_action"] == "clarify_intent"
        assert selected.status_code == 200
        assert selected.json()["phase"] == "completed"
        assert selected.json()["intent"] == "tracking"
        assert len(gateway.commands) == 1

    asyncio.run(scenario())


def test_owner_isolation_and_idempotent_deletion(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with create_persistent_agent(
            database_path=tmp_path / "delete.db",
            tracking_gateway=FakeTrackingGateway(),
            clock=lambda: NOW,
        ) as components:
            async with await _client_for(components) as client:
                created = await client.post(
                    "/v2/agent/messages",
                    headers={
                        **AUTH_A,
                        "Idempotency-Key": "delete-start",
                    },
                    json={"message": "查邮件"},
                )
                conversation_id = created.json()["conversation_id"]
                forbidden = await client.post(
                    "/v2/agent/messages",
                    headers={
                        **AUTH_B,
                        "Idempotency-Key": "foreign-resume",
                    },
                    json={
                        "conversation_id": conversation_id,
                        "message": MAIL_NO,
                    },
                )
                deleted = await client.delete(
                    f"/v2/agent/conversations/{conversation_id}",
                    headers=AUTH_A,
                )
                deleted_again = await client.delete(
                    f"/v2/agent/conversations/{conversation_id}",
                    headers=AUTH_A,
                )
                unavailable = await client.post(
                    "/v2/agent/messages",
                    headers={
                        **AUTH_A,
                        "Idempotency-Key": "after-delete",
                    },
                    json={
                        "conversation_id": conversation_id,
                        "message": MAIL_NO,
                    },
                )

        assert forbidden.status_code == 404
        assert forbidden.json()["detail"]["code"] == (
            "conversation_not_available"
        )
        assert deleted.status_code == 204
        assert deleted_again.status_code == 204
        assert unavailable.status_code == 404

    asyncio.run(scenario())


def test_agent_api_rejects_invalid_payloads_and_accepts_streaming(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with create_persistent_agent(
            database_path=tmp_path / "validation.db",
            tracking_gateway=FakeTrackingGateway(),
            clock=lambda: NOW,
        ) as components:
            async with await _client_for(components) as client:
                missing_header = await client.post(
                    "/v2/agent/messages",
                    headers=AUTH_A,
                    json={"message": "查邮件"},
                )
                empty = await client.post(
                    "/v2/agent/messages",
                    headers={**AUTH_A, "Idempotency-Key": "empty"},
                    json={},
                )
                unknown = await client.post(
                    "/v2/agent/messages",
                    headers={**AUTH_A, "Idempotency-Key": "unknown"},
                    json={
                        "message": "查一下",
                        "explicit_intent": "unknown",
                    },
                )
                extra = await client.post(
                    "/v2/agent/messages",
                    headers={**AUTH_A, "Idempotency-Key": "extra"},
                    json={"message": "查邮件", "history": []},
                )
                stream = await client.post(
                    "/v2/agent/messages",
                    headers={**AUTH_A, "Idempotency-Key": "stream"},
                    json={"message": "查邮件", "stream": True},
                )
                intent_only_new = await client.post(
                    "/v2/agent/messages",
                    headers={
                        **AUTH_A,
                        "Idempotency-Key": "intent-only-new",
                    },
                    json={"explicit_intent": "tracking"},
                )
                v1_invalid = await client.post(
                    "/v1/chat",
                    headers=AUTH_A,
                    json={},
                )

        assert missing_header.status_code == 422
        assert missing_header.json()["detail"]["code"] == (
            "invalid_agent_request"
        )
        assert empty.status_code == 422
        assert empty.json()["detail"]["code"] == "invalid_agent_request"
        assert unknown.status_code == 422
        assert extra.status_code == 422
        assert stream.status_code == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        assert "event: input_required" in stream.text
        assert "event: done" in stream.text
        assert intent_only_new.status_code == 422
        assert intent_only_new.json()["detail"]["code"] == (
            "message_required_for_new_conversation"
        )
        assert v1_invalid.status_code == 422
        assert isinstance(v1_invalid.json()["detail"], list)

    asyncio.run(scenario())


def test_expired_conversation_maps_to_stable_conflict(
    tmp_path: Path,
) -> None:
    current = [NOW]

    async def scenario() -> None:
        async with create_persistent_agent(
            database_path=tmp_path / "expiry-api.db",
            tracking_gateway=FakeTrackingGateway(),
            conversation_ttl=timedelta(minutes=1),
            clock=lambda: current[0],
        ) as components:
            async with await _client_for(components) as client:
                created = await client.post(
                    "/v2/agent/messages",
                    headers={
                        **AUTH_A,
                        "Idempotency-Key": "expiry-create",
                    },
                    json={"message": "查邮件"},
                )
                current[0] = NOW + timedelta(minutes=2)
                expired = await client.post(
                    "/v2/agent/messages",
                    headers={
                        **AUTH_A,
                        "Idempotency-Key": "expiry-resume",
                    },
                    json={
                        "conversation_id": created.json()[
                            "conversation_id"
                        ],
                        "message": MAIL_NO,
                    },
                )

        assert expired.status_code == 409
        assert expired.json()["detail"]["code"] == "conversation_expired"
        assert expired.json()["detail"]["category"] == "state_conflict"

    asyncio.run(scenario())


def test_outer_timeout_releases_message_claim_for_safe_retry(
    tmp_path: Path,
) -> None:
    gateway = TimeoutOnceTrackingGateway()

    async def scenario() -> None:
        async with create_persistent_agent(
            database_path=tmp_path / "timeout-retry.db",
            tracking_gateway=gateway,
            clock=lambda: NOW,
        ) as components:
            app = create_app(
                settings=_settings(),
                agent_api=AgentApiDependencies(
                    service=components.service,
                    capabilities=(
                        components.runtime.capability_descriptors
                    ),
                    run_timeout_seconds=0.5,
                ),
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://assistant.test",
            ) as client:
                first = await client.post(
                    "/v2/agent/messages",
                    headers={
                        **AUTH_A,
                        "Idempotency-Key": "timeout-then-retry",
                    },
                    json={
                        "message": f"查询邮件 {MAIL_NO}",
                        "explicit_intent": "tracking",
                    },
                )
                retried = await client.post(
                    "/v2/agent/messages",
                    headers={
                        **AUTH_A,
                        "Idempotency-Key": "timeout-then-retry",
                    },
                    json={
                        "message": f"查询邮件 {MAIL_NO}",
                        "explicit_intent": "tracking",
                    },
                )

        assert first.status_code == 504
        assert first.json()["detail"]["code"] == "agent_request_timeout"
        assert retried.status_code == 200
        assert retried.json()["phase"] == "completed"
        assert len(gateway.commands) == 2

    asyncio.run(scenario())
