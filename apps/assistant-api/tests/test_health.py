from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from spb_assistant_api.api.app import _default_tools, create_app
from spb_assistant_api.domain.models import QueryMode, ToolResult, ToolStatus
from spb_assistant_api.services.dispatcher import (
    DEVICE_PRICE_TOOL_NAME,
    POLICY_TOOL_NAME,
)
from spb_assistant_api.settings import AssistantSettings
from spb_assistant_api.tools.device_price import DevicePriceTool
from spb_assistant_api.tools.policy import PolicyKnowledgeTool
from spb_assistant_api.tools.unavailable import UnavailableTool

from .fakes import FakeTool


def _tool(name: str) -> FakeTool:
    return FakeTool(
        name=name,
        result=ToolResult(
            tool=name,
            status=ToolStatus.SUCCESS,
            answer="ok",
        ),
    )


def test_live_reports_explicit_modes_and_disabled_memory() -> None:
    app = create_app(
        settings=AssistantSettings(
            auth_enabled=False,
            rate_limit_enabled=False,
        )
    )

    with TestClient(app) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    payload = response.json()
    assert payload["phase"] == 3
    assert payload["checks"]["routing"] == "explicit"
    assert payload["checks"]["memory"] == "disabled"
    assert payload["checks"]["query_modes"] == "policy,device_price"


def test_ready_fails_for_default_unavailable_tools() -> None:
    app = create_app(
        settings=AssistantSettings(
            auth_enabled=False,
            rate_limit_enabled=False,
        )
    )

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    checks = response.json()["checks"]
    assert checks[POLICY_TOOL_NAME] == "not_ready"
    assert checks[DEVICE_PRICE_TOOL_NAME] == "not_ready"


def test_ready_succeeds_when_both_explicit_tools_are_ready() -> None:
    app = create_app(
        settings=AssistantSettings(
            auth_enabled=False,
            rate_limit_enabled=False,
        ),
        tools={
            QueryMode.POLICY: _tool(POLICY_TOOL_NAME),
            QueryMode.DEVICE_PRICE: _tool(DEVICE_PRICE_TOOL_NAME),
        },
    )

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_default_tools_enable_only_configured_price_source() -> None:
    tools = _default_tools(
        AssistantSettings(
            auth_enabled=False,
            mysql_dsn=(
                "mysql+pymysql://readonly:secret@mysql/device_price"
            ),
        )
    )

    assert isinstance(tools[QueryMode.POLICY], UnavailableTool)
    assert isinstance(tools[QueryMode.DEVICE_PRICE], DevicePriceTool)


def test_default_tools_enable_policy_only_with_url_and_service_key() -> None:
    tools = _default_tools(
        AssistantSettings(
            auth_enabled=False,
            rag_base_url="http://rag-api:8080",
            rag_api_key="internal-rag-key",
        )
    )

    assert isinstance(tools[QueryMode.POLICY], PolicyKnowledgeTool)
    assert isinstance(tools[QueryMode.DEVICE_PRICE], UnavailableTool)
    asyncio.run(tools[QueryMode.POLICY].close())


def test_default_tools_do_not_enable_policy_without_service_key() -> None:
    tools = _default_tools(
        AssistantSettings(
            auth_enabled=False,
            rag_base_url="http://rag-api:8080",
        )
    )

    assert isinstance(tools[QueryMode.POLICY], UnavailableTool)


def test_settings_reject_non_mysql_price_dsn() -> None:
    with pytest.raises(ValidationError, match=r"mysql\+pymysql"):
        AssistantSettings(mysql_dsn="sqlite:///price.db")


def test_settings_reject_invalid_rag_url_and_candidate_limits() -> None:
    with pytest.raises(ValidationError, match="rag_base_url"):
        AssistantSettings(
            rag_base_url="http://user:secret@rag-api:8080?token=secret"
        )
    with pytest.raises(ValidationError, match="rag_top_k"):
        AssistantSettings(rag_top_k=50, rag_candidate_k=40)
