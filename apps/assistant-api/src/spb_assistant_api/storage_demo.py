"""Synthetic finite storage drill. No HTTP server, settings, dotenv or network.

Use only a dedicated demo store. Stable creation and message keys prove that
restore retains both metadata and execution receipts, not merely graph state.
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta

from .adapters.fake_tracking import FakeTrackingGateway
from .domain.agent_errors import AgentOperationError
from .domain.results import TrackingData, TrackingEvent
from .services.query_understanding import RuleBasedQueryUnderstander
from .storage_paths import DATABASE_NAME, StorageError, init_store, validate_store
from .workflow.composition import create_persistent_agent
from .workflow.conversation_service import request_fingerprint

OWNER = "storage-demo-synthetic-owner"
MAIL = "1234567890123"


def require(condition: bool) -> None:
    if not condition:
        raise StorageError("demo_assertion_failed", "合成恢复验收未通过")


async def drill(directory, operation: str) -> dict:
    if operation == "seed":
        init_store(directory, if_absent=True)
    store, _ = validate_store(directory)
    observed = datetime.now(UTC)
    gateway = FakeTrackingGateway({MAIL: TrackingData(
        mail_no=MAIL, current_status="合成运输节点", events=[TrackingEvent(description="合成演练", occurred_at=observed)], queried_at=observed,
    )})
    async with create_persistent_agent(
        database_path=store / DATABASE_NAME, managed_storage=True,
        tracking_gateway=gateway, understander=RuleBasedQueryUnderstander(),
        conversation_ttl=timedelta(hours=1), workflow_trace_sink=None,
    ) as parts:
        completed = await parts.service.create_conversation_idempotently(
            owner_id=OWNER, idempotency_key="demo-completed", request_hash=request_fingerprint({"fixture": "completed-v1"}),
        )
        pending = await parts.service.create_conversation_idempotently(
            owner_id=OWNER, idempotency_key="demo-pending", request_hash=request_fingerprint({"fixture": "pending-v1"}),
        )
        result = await parts.service.send_message(
            conversation_id=completed.conversation_id, owner_id=OWNER,
            idempotency_key="query-completed", message="查邮件轨迹 " + MAIL,
        )
        require(result["phase"] == "completed")
        if operation == "seed":
            paused = await parts.service.send_message(
                conversation_id=pending.conversation_id, owner_id=OWNER,
                idempotency_key="query-pending", message="查邮件轨迹",
            )
            require(paused["phase"] == "waiting_user" and len(gateway.commands) in {0, 1})
        else:
            require(len(gateway.commands) == 0)  # Completed query must replay, not call Fake again.
            resumed = await parts.service.send_message(
                conversation_id=pending.conversation_id, owner_id=OWNER,
                idempotency_key="finish-pending", message=MAIL,
            )
            require(resumed["phase"] == "completed")
            require(len(gateway.commands) == (1 if operation == "resume" else 0))
        require(all(value == "ready" for value in (await parts.readiness.check()).values()))
    validate_store(store)
    return {"status": "ok", "operation": operation, "uid": os.geteuid(), "synthetic_tool_calls": len(gateway.commands), "network_calls": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("seed", "resume", "replay"))
    parser.add_argument("--directory", required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(asyncio.run(drill(args.directory, args.operation)), sort_keys=True))
        return 0
    except (StorageError, AgentOperationError, OSError):
        print('{"status":"failed","code":"storage_demo_failed"}', file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
