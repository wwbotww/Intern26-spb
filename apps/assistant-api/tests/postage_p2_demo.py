"""Run from apps/assistant-api: ../../.venv/bin/python -m tests.postage_p2_demo.

Uses only synthetic fixtures and MockTransport, and a disposable SQLite store.
No model, dotenv, provider endpoint, API server, or production factory.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory

from spb_assistant_api.workflow.composition import create_persistent_agent

from .postage_p1_fixture import NOW
from .postage_p2_fixture import bound_policy, gateway, response_for


async def smoke(database: Path) -> dict[str, object]:
    calls = []
    ticks = count()
    serials = count()
    client = gateway(
        lambda request: calls.append(request) or response_for(request),
        clock=lambda: NOW + timedelta(seconds=next(ticks)),
        serial_number_factory=lambda: f"synthetic-demo-{next(serials)}",
    )
    kwargs = dict(
        database_path=database, postage_gateway=client, postage_preflight=bound_policy(),
        clock=lambda: NOW, workflow_trace_sink=None,
    )
    try:
        async with create_persistent_agent(**kwargs) as parts:
            waiting = await parts.runtime.start(thread_id="synthetic-demo", message="北京寄上海 1.25 kg 查资费")
            assert waiting["phase"] == "waiting_user" and len(calls) == 0
        async with create_persistent_agent(**kwargs) as parts:
            done = await parts.runtime.resume(thread_id="synthetic-demo", message="SYN-A")
            assert done["phase"] == "completed" and len(calls) == 1
            history = [s async for s in parts.runtime.graph.aget_state_history(parts.runtime.config("synthetic-demo"))]
            before = next(s for s in history if s.next == ("execute_tool",))
            replay = await parts.runtime.graph.ainvoke(None, config=before.config)
            assert replay["result"] == done["result"] and len(calls) == 1
            fresh = await parts.runtime.start(thread_id="synthetic-demo", message="北京寄上海 1.25 kg 查资费 SYN-A")
            assert fresh["phase"] == "completed" and len(calls) == 2
            assert fresh["result"]["data"]["queried_at"] != done["result"]["data"]["queried_at"]
            return {
                "classification": "synthetic-only-offline", "provider_verified": False,
                "live_requests": 0, "mock_requests": len(calls),
                "path": ["collect_product", "sqlite_restart", "signed_form", "four_layer_validation", "quote", "receipt_replay_no_call", "fresh_query"],
                "example_quote": {
                    "amount": done["result"]["data"]["amount"],
                    "currency": done["result"]["data"]["currency"],
                    "product": done["result"]["data"]["product_code"],
                    "warning": "合成资费，不可用于实际寄递；币种和金额口径尚未由接口方确认。",
                },
            }
    finally:
        await client.close()


def main() -> None:
    with TemporaryDirectory(prefix="postage-p2-offline-") as directory:
        result = asyncio.run(smoke(Path(directory) / "agent.db"))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
