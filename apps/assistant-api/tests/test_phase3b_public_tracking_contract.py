from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from spb_assistant_api.api.agent_schemas import AgentResultResponse, AgentSourceResponse
from spb_assistant_api.domain.intents import Intent
from spb_assistant_api.domain.results import AgentResult, AgentResultStatus, SourceReference
from spb_eval.schemas import AgentResultObservation, AgentSourceObservation


def test_public_projection_and_eval_only_accept_the_source_allowlist():
    result = AgentResult(
        tool="tracking", intent=Intent.TRACKING, status=AgentResultStatus.NO_MATCH,
        answer="没有返回记录，不代表不存在",
        provenance=[SourceReference(
            source_type="external_api", source_name="postal-tracking",
            source_profile="postal-tracking-doc-2019-v1",
            queried_at=datetime(2026, 9, 9, tzinfo=UTC),
            source_url="https://private-canary.test/secret", record_id="private-canary",
        )],
    )
    public = AgentResultResponse.from_domain(result).model_dump(mode="json")
    assert "private-canary" not in json.dumps(public)
    assert public["provenance"][0]["history_completeness"] == "unknown"
    assert AgentResultObservation.model_validate(public).model_dump(mode="json") == public
    assert set(public["provenance"][0]) == {
        "source_type", "source_name", "source_profile", "queried_at", "history_completeness",
    }
    old = {key: value for key, value in public.items() if key != "provenance"}
    assert AgentResultResponse.model_validate(old).provenance == []
    assert AgentResultObservation.model_validate(old).provenance == []


@pytest.mark.parametrize("source_type,name", [
    ("fake_gateway", "历史演示来源"), ("provider-canary", "untrusted"),
    ("external_api", "https://private-canary.test"),
])
def test_unknown_legacy_source_is_not_invented_or_exposed(source_type, name):
    source = AgentSourceResponse.from_tracking_source(SourceReference(
        source_type=source_type, source_name=name, history_completeness="complete",
    ))
    assert source.source_type == "unknown"
    assert source.source_name == "legacy-unknown"
    assert source.source_profile == "" and source.history_completeness == "unknown"


@pytest.mark.parametrize("changes", [
    {"source_type": "forged"}, {"source_name": "https://private.test"},
    {"source_profile": "x" * 129}, {"history_completeness": "probably"},
    {"queried_at": "2026-09-09T10:00:00"}, {"source_url": "https://private.test"},
])
def test_server_and_independent_eval_mirror_reject_malformed_sources(changes):
    data = {"source_type": "external_api", "source_name": "postal-tracking", **changes}
    for schema in (AgentSourceResponse, AgentSourceObservation):
        with pytest.raises(ValidationError):
            schema.model_validate(data)


def test_openapi_source_schema_matches_backend_and_eval_mirror():
    contract = Path(__file__).resolve().parents[3] / "docs/openapi/assistant-agent-v2.openapi.json"
    schemas = json.loads(contract.read_text(encoding="utf-8"))["components"]["schemas"]
    declared = schemas["AgentSourceResponse"]
    for runtime in (AgentSourceResponse.model_json_schema(), AgentSourceObservation.model_json_schema()):
        assert set(runtime["properties"]) == set(declared["properties"])
        assert set(runtime["required"]) == set(declared["required"])
        for name, schema in declared["properties"].items():
            actual = dict(runtime["properties"][name])
            actual.pop("title", None)
            assert actual == schema
    assert schemas["AgentResult"]["properties"]["provenance"]["items"]["$ref"].endswith("/AgentSourceResponse")
