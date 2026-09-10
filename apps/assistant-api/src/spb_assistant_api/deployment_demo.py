"""Loopback-only HTTPS deployment fixture, never imported by deployed_app/main.

Only AGENT_DEMO_* selects origin, UI mode and a managed database. No settings
environment, dotenv, real transport, model or telemetry exporter is consulted.
"""

import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn
from pydantic_settings import SettingsConfigDict

from .adapters.fake_tracking import FakeTrackingGateway
from .api.agent_contracts import AgentApiDependencies
from .api.app import create_app
from .domain.models import PolicyEvidence, QueryMode, ToolResult, ToolStatus
from .domain.results import TrackingData, TrackingEvent
from .services.dispatcher import EXPECTED_TOOL_NAMES
from .services.query_understanding import RuleBasedQueryUnderstander
from .observability.metrics import ServiceMetrics
from .observability.telemetry import WorkflowTelemetry
from .security.browser_session import BrowserSessionConfig
from .settings import AssistantSettings
from .workflow.composition import create_persistent_agent

PROXY_KEY = "synthetic-deployment-proxy-6a3"
SIGNING_KEY = "synthetic-deployment-signing-6a3-not-a-production-secret"
MAIL = "1234567890123"


class _SyntheticSettings(AssistantSettings):
    model_config = SettingsConfigDict(env_file=None)

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        return (init_settings,)


class _SyntheticLegacyTool:
    def __init__(self, mode):
        self.name = EXPECTED_TOOL_NAMES[mode]
        self._mode = mode

    async def initialize(self):
        pass

    async def close(self):
        pass

    def readiness(self):
        return "ready"

    async def execute(self, question):
        # Fixed contract fixture; never infer or advertise actual prices/policy.
        if self._mode == QueryMode.POLICY:
            return ToolResult(
                tool=self.name, status=ToolStatus.SUCCESS, answer="仅为部署合成演练，不是政策建议[1]。",
                evidence=(PolicyEvidence(
                    evidence_id="synthetic-1", title="合成部署夹具", source_url="https://example.invalid/fixture",
                    excerpt="仅验证部署与回退，不包含真实政策内容。", chunk_id="synthetic-1", document_id="synthetic-1",
                ),),
            )
        return ToolResult(tool=self.name, status=ToolStatus.NO_MATCH, answer="合成部署演练不提供真实设备价格。")


def create_deployment_demo(*, database_path: Path, public_origin: str, ui_mode: str = "agent", transport_mode: str = "https"):
    parsed = urlsplit(public_origin)
    if transport_mode not in {"https", "private-http"}:
        raise ValueError("未知的合成传输模式")
    secure = transport_mode == "https"
    if parsed.scheme != ("https" if secure else "http") or parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise ValueError("合成部署仅接受显式模式对应的本机 origin")
    if ui_mode not in {"agent", "legacy"}:
        raise ValueError("未知的合成 UI 模式")
    BrowserSessionConfig(public_origin=public_origin, proxy_api_key=PROXY_KEY, signing_key=SIGNING_KEY, secure=secure, private_http_enabled=not secure)
    enabled = ui_mode == "agent"
    settings = _SyntheticSettings(
        auth_enabled=True, api_keys=PROXY_KEY, rate_limit_requests=600,
        agent_enabled=enabled, agent_managed_storage_enabled=enabled,
        agent_database_path=str(database_path),
        agent_browser_session_enabled=enabled,
        agent_browser_public_origin=public_origin,
        agent_browser_cookie_secure=secure,
        agent_browser_private_http_enabled=not secure,
        agent_browser_proxy_api_key=PROXY_KEY, agent_browser_signing_key=SIGNING_KEY,
    )
    legacy = {mode: _SyntheticLegacyTool(mode) for mode in QueryMode}
    metrics = ServiceMetrics()
    now = datetime.now(UTC)
    gateway = FakeTrackingGateway({MAIL: TrackingData(
        mail_no=MAIL, current_status="合成运输节点", queried_at=now,
        events=[TrackingEvent(description="部署验收合成事件", occurred_at=now)],
    )})

    @asynccontextmanager
    async def dependencies():
        async with create_persistent_agent(
            database_path=database_path, managed_storage=True,
            tracking_gateway=gateway, understander=RuleBasedQueryUnderstander(),
            policy_tool=legacy[QueryMode.POLICY], device_price_tool=legacy[QueryMode.DEVICE_PRICE],
            workflow_trace_sink=None,
            telemetry=WorkflowTelemetry(metrics),
        ) as parts:
            yield AgentApiDependencies(
                service=parts.service, capabilities=parts.runtime.capability_descriptors,
                readiness_probe=parts.readiness, janitor=parts.janitor,
            )

    app = create_app(settings=settings, tools=legacy, agent_api_factory=dependencies if enabled else None, metrics=metrics)
    # In-process tests can count Fake calls; no HTTP debug or fixture control API.
    app.state.synthetic_tracking = gateway
    return app


def build_app():
    return create_deployment_demo(
        database_path=Path(os.environ["AGENT_DEMO_DATABASE_PATH"]),
        public_origin=os.environ["AGENT_DEMO_PUBLIC_ORIGIN"],
        ui_mode=os.environ.get("AGENT_DEMO_UI_MODE", "agent"),
        transport_mode=os.environ.get("AGENT_DEMO_TRANSPORT_MODE", "https"),
    )


if __name__ == "__main__":
    uvicorn.run(build_app(), host="0.0.0.0", port=8081, workers=1, proxy_headers=False, access_log=False)
