"""Local browser QA only: actual postal adapter over MockTransport, no live I/O.

Run with --factory and SPB_TRACKING_SMOKE_DB pointing to a temporary absolute DB.
No developer dotenv, RAG, MySQL, model, telemetry exporter, or Fake fallback.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from urllib.parse import parse_qs

import httpx

from spb_assistant_api.api.app import create_app
from spb_assistant_api.configured_agent import create_configured_agent_factory
from spb_assistant_api.domain.models import QueryMode
from spb_assistant_api.observability.metrics import ServiceMetrics
from spb_assistant_api.settings import AssistantSettings
from spb_assistant_api.tools.unavailable import UnavailableTool


def create_fixture_app():
    settings = AssistantSettings(
        _env_file=None, agent_enabled=True,
        agent_database_path=os.environ["SPB_TRACKING_SMOKE_DB"],
        auth_enabled=True, api_keys="synthetic-browser-only",
        rate_limit_enabled=False, query_model_enabled=False, otel_enabled=False,
        rag_base_url="", rag_api_key="", mysql_dsn="",
        tracking_enabled=True, tracking_base_url="https://postal.example.test",
        tracking_path="interface", tracking_send_id="SYNTHETIC",
        tracking_receive_id="JDPT", tracking_msg_kind="SYNTHETIC_TRACE",
        tracking_signing_system_id="synthetic-only",
        tracking_expected_response_receive_id="SYNTHETIC",
        tracking_timezone="Asia/Shanghai", tracking_province_no="99",
        tracking_profile="postal-tracking-doc-2019-v1",
        tracking_timeout_seconds=15,
    )
    calls: dict[str, int] = {}

    async def handler(request):
        mail = json.loads(parse_qs(request.content.decode())["msgBody"][0])["traceNo"]
        calls[mail] = calls.get(mail, 0) + 1
        body = json.loads((Path(__file__).parent / "fixtures/postal_tracking/success.json").read_text(encoding="utf-8"))
        if mail == "0000000000000":
            body["responseItems"] = []
        elif mail == "1111111111111":
            return httpx.Response(429, headers={"Retry-After": "0"})
        elif mail == "2222222222222":
            return httpx.Response(200, json={"invalid": "synthetic"})
        else:
            if mail == "3333333333333":
                await asyncio.sleep(10)  # Deliberate local stop-reading/replay QA.
            for event in body["responseItems"]:
                event["traceNo"] = mail
            if calls[mail] > 1:
                body["responseItems"][0].update(opName="妥投（合成测试）", opDesc="本地 Mock 第二次查询：合成妥投节点")
        return httpx.Response(200, json=body)

    tools = {
        QueryMode.POLICY: UnavailableTool("policy_knowledge"),
        QueryMode.DEVICE_PRICE: UnavailableTool("device_price"),
    }
    metrics = ServiceMetrics()
    return create_app(
        settings=settings, tools=tools, metrics=metrics,
        agent_api_factory=create_configured_agent_factory(
            settings=settings, legacy_tools=tools, metrics=metrics,
            tracking_transport=httpx.MockTransport(handler),
        ),
    )
