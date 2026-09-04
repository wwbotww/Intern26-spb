from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from spb_assistant_api.adapters.fake_tracking import FakeTrackingGateway
from spb_assistant_api.adapters.sqlite_persistence import (
    create_sqlite_agent_repositories,
)
from spb_assistant_api.domain.agent_errors import AgentOperationError
from spb_assistant_api.domain.commands import TrackingCommand
from spb_assistant_api.domain.conversations import (
    ConversationMetadata,
    ConversationStatus,
    IdempotencyClaimStatus,
)
from spb_assistant_api.domain.results import TrackingData
from spb_assistant_api.workflow.composition import (
    create_persistent_tracking_agent,
)
from spb_assistant_api.workflow.migrations import AgentStateMigrator


NOW = datetime(2026, 9, 3, 8, tzinfo=UTC)
MAIL_NO = "1234567890123"
CONVERSATION_ID = UUID("11111111-1111-4111-8111-111111111111")
REQUEST_HASH = "sha256:" + "a" * 64


def _metadata(*, expires_at: datetime | None = None) -> ConversationMetadata:
    return ConversationMetadata(
        conversation_id=CONVERSATION_ID,
        owner_id="principal-1",
        created_at=NOW,
        updated_at=NOW,
        expires_at=expires_at or NOW + timedelta(minutes=30),
    )


def _tracking_data() -> TrackingData:
    return TrackingData(
        mail_no=MAIL_NO,
        current_status="运输中",
        queried_at=NOW,
    )


def test_metadata_and_idempotency_receipt_survive_connection_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "metadata.db"

    async def scenario() -> None:
        async with create_sqlite_agent_repositories(database) as first:
            await first.metadata.create(_metadata())
            claim = await first.metadata.claim_idempotency(
                conversation_id=CONVERSATION_ID,
                key="request-1",
                request_hash=REQUEST_HASH,
                now=NOW,
            )
            assert claim.status is IdempotencyClaimStatus.CLAIMED
            await first.metadata.complete_idempotency(
                conversation_id=CONVERSATION_ID,
                key="request-1",
                request_hash=REQUEST_HASH,
                response={"phase": "waiting_user"},
                completed_at=NOW,
            )

        async with create_sqlite_agent_repositories(database) as second:
            metadata = await second.metadata.get(CONVERSATION_ID)
            replay = await second.metadata.claim_idempotency(
                conversation_id=CONVERSATION_ID,
                key="request-1",
                request_hash=REQUEST_HASH,
                now=NOW,
            )
            conflict = await second.metadata.claim_idempotency(
                conversation_id=CONVERSATION_ID,
                key="request-1",
                request_hash="sha256:" + "b" * 64,
                now=NOW,
            )

            assert metadata == _metadata()
            assert replay.status is IdempotencyClaimStatus.REPLAY
            assert replay.receipt.response == {"phase": "waiting_user"}
            assert conflict.status is IdempotencyClaimStatus.CONFLICT

    asyncio.run(scenario())


def test_interrupted_graph_resumes_after_sqlite_restart_without_duplicate_tool(
    tmp_path: Path,
) -> None:
    database = tmp_path / "agent.db"
    first_gateway = FakeTrackingGateway({MAIL_NO: _tracking_data()})

    async def scenario() -> None:
        async with create_persistent_tracking_agent(
            database_path=database,
            gateway=first_gateway,
            clock=lambda: NOW,
        ) as first:
            await first.service.create_conversation(
                owner_id="principal-1",
                conversation_id=CONVERSATION_ID,
            )
            waiting = await first.service.start(
                conversation_id=CONVERSATION_ID,
                owner_id="principal-1",
                idempotency_key="start-1",
                message="查一下邮件",
            )
            assert waiting["phase"] == "waiting_user"
            assert "__interrupt__" not in waiting
            assert first_gateway.commands == []

        restarted_gateway = FakeTrackingGateway(
            {MAIL_NO: _tracking_data()}
        )
        async with create_persistent_tracking_agent(
            database_path=database,
            gateway=restarted_gateway,
            clock=lambda: NOW,
        ) as restarted:
            completed = await restarted.service.resume(
                conversation_id=CONVERSATION_ID,
                owner_id="principal-1",
                idempotency_key="resume-1",
                message=MAIL_NO,
            )
            replay = await restarted.service.resume(
                conversation_id=CONVERSATION_ID,
                owner_id="principal-1",
                idempotency_key="resume-1",
                message=MAIL_NO,
            )

            assert completed["phase"] == "completed"
            assert replay == completed
            assert len(restarted_gateway.commands) == 1

        replay_gateway = FakeTrackingGateway({MAIL_NO: _tracking_data()})
        async with create_persistent_tracking_agent(
            database_path=database,
            gateway=replay_gateway,
            clock=lambda: NOW,
        ) as replayed_process:
            replayed_tool_result = await replayed_process.service.start(
                conversation_id=CONVERSATION_ID,
                owner_id="principal-1",
                idempotency_key="start-same-tool-new-request",
                message=f"查邮件 {MAIL_NO}",
            )
            assert replayed_tool_result["phase"] == "completed"
            assert replay_gateway.commands == []

    asyncio.run(scenario())


class BlockingTrackingGateway:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.commands: list[TrackingCommand] = []

    async def query(
        self,
        command: TrackingCommand,
    ) -> TrackingData | None:
        self.commands.append(command)
        self.started.set()
        await self.release.wait()
        return _tracking_data()


def test_concurrent_resume_is_rejected_before_second_tool_call(
    tmp_path: Path,
) -> None:
    database = tmp_path / "concurrency.db"

    async def scenario() -> None:
        current = [NOW]
        gateway = BlockingTrackingGateway()
        async with create_persistent_tracking_agent(
            database_path=database,
            gateway=gateway,
            conversation_ttl=timedelta(minutes=1),
            clock=lambda: current[0],
        ) as components:
            await components.service.create_conversation(
                owner_id="principal-1",
                conversation_id=CONVERSATION_ID,
            )
            await components.service.start(
                conversation_id=CONVERSATION_ID,
                owner_id="principal-1",
                idempotency_key="start-concurrent",
                message="查邮件",
            )
            first = asyncio.create_task(
                components.service.resume(
                    conversation_id=CONVERSATION_ID,
                    owner_id="principal-1",
                    idempotency_key="resume-a",
                    message=MAIL_NO,
                )
            )
            await gateway.started.wait()
            with pytest.raises(AgentOperationError) as raised:
                await components.service.resume(
                    conversation_id=CONVERSATION_ID,
                    owner_id="principal-1",
                    idempotency_key="resume-b",
                    message=MAIL_NO,
                )
            assert raised.value.failure.code == (
                "conversation_run_in_progress"
            )
            current[0] = NOW + timedelta(minutes=2)
            cleanup = await components.janitor.cleanup_expired()
            assert cleanup.expired_conversations == 0
            assert cleanup.failures == (str(CONVERSATION_ID),)
            gateway.release.set()
            result = await first
            assert result["phase"] == "completed"
            assert len(gateway.commands) == 1

    asyncio.run(scenario())


def test_expired_conversation_cleanup_removes_checkpoint_and_receipts(
    tmp_path: Path,
) -> None:
    database = tmp_path / "expiry.db"
    current = [NOW]
    gateway = FakeTrackingGateway({MAIL_NO: _tracking_data()})

    async def scenario() -> None:
        async with create_persistent_tracking_agent(
            database_path=database,
            gateway=gateway,
            conversation_ttl=timedelta(minutes=1),
            clock=lambda: current[0],
        ) as components:
            await components.service.create_conversation(
                owner_id="principal-1",
                conversation_id=CONVERSATION_ID,
            )
            await components.service.start(
                conversation_id=CONVERSATION_ID,
                owner_id="principal-1",
                idempotency_key="expiry-start",
                message=f"查邮件 {MAIL_NO}",
            )
            assert len(gateway.commands) == 1
            current[0] = NOW + timedelta(minutes=2)

            cleanup = await components.janitor.cleanup_expired()
            snapshot = await components.runtime.graph.aget_state(
                components.runtime.config(str(CONVERSATION_ID))
            )

            assert cleanup.expired_conversations == 1
            assert cleanup.deleted_idempotency_receipts == 1
            assert cleanup.deleted_tool_receipts == 1
            assert not snapshot.values

        async with create_sqlite_agent_repositories(database) as repositories:
            metadata = await repositories.metadata.get(CONVERSATION_ID)
            assert metadata is not None
            assert metadata.status is ConversationStatus.DELETED

    asyncio.run(scenario())


def test_expired_conversation_cannot_resume_or_call_tool(
    tmp_path: Path,
) -> None:
    database = tmp_path / "expired-resume.db"
    current = [NOW]
    gateway = FakeTrackingGateway({MAIL_NO: _tracking_data()})

    async def scenario() -> None:
        async with create_persistent_tracking_agent(
            database_path=database,
            gateway=gateway,
            conversation_ttl=timedelta(minutes=1),
            clock=lambda: current[0],
        ) as components:
            await components.service.create_conversation(
                owner_id="principal-1",
                conversation_id=CONVERSATION_ID,
            )
            await components.service.start(
                conversation_id=CONVERSATION_ID,
                owner_id="principal-1",
                idempotency_key="expired-start",
                message="查邮件",
            )
            current[0] = NOW + timedelta(minutes=2)

            with pytest.raises(AgentOperationError) as raised:
                await components.service.resume(
                    conversation_id=CONVERSATION_ID,
                    owner_id="principal-1",
                    idempotency_key="expired-resume",
                    message=MAIL_NO,
                )

            assert raised.value.failure.code == "conversation_expired"
            assert gateway.commands == []

    asyncio.run(scenario())


def test_state_schema_migration_is_additive_and_rejects_future_version() -> None:
    migrator = AgentStateMigrator()

    migrated = migrator.migrate(
        {
            "schema_version": "1",
            "conversation_id": str(CONVERSATION_ID),
            "active_intent": "tracking",
        }
    )

    assert migrated.changed
    assert migrated.state["schema_version"] == "2"
    assert migrated.state["slot_provenance"] == []
    assert migrated.state["multi_intent"] is False

    with pytest.raises(AgentOperationError) as raised:
        migrator.migrate({"schema_version": "999"})
    assert raised.value.failure.code == "unsupported_agent_state_schema"
