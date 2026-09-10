import asyncio
import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from spb_assistant_api.api.agent_schemas import AgentResultResponse, PostageQuoteBasisResponse
from spb_assistant_api.tools.postage import PostageTool
from spb_eval.schemas import AgentResultObservation, PostageQuoteBasisObservation

from .postage_p1_fixture import command, ObservedFakePostageGateway, preflight


def public_quote():
    policy = preflight()
    prepared = policy.prepare(command())
    async def run():
        gateway = ObservedFakePostageGateway()
        # Public projection is tested with the same validated tool result as V2.
        return await PostageTool(gateway, preflight=policy).execute(prepared)
    return AgentResultResponse.from_domain(asyncio.run(run())).model_dump(mode="json")


def test_public_projection_hides_internal_context_and_mirrors_eval():
    public = public_quote()
    serialized = json.dumps(public)
    for private in ("pricing_context", "policy_fingerprint", "billing_id", "catalog_version", "history_completeness"):
        assert private not in serialized
    assert public["provenance"] == []
    assert public["quote_basis"]["is_estimate"] is True
    assert public["quote_basis"]["scope"] == "domestic_actual_weight_no_extras"
    assert AgentResultObservation.model_validate(public).model_dump(mode="json") == public
    public.pop("quote_basis")
    for model in (AgentResultResponse, AgentResultObservation):
        assert model.model_validate(public).quote_basis is None


@pytest.mark.parametrize("path,value", [
    ("type", "tracking"), ("status", "partial"), ("data", None),
    ("data.amount", "NaN"), ("data.amount", 12.30), ("data.amount", "-1.00"),
    ("data.currency", "USD"), ("data.product_code", "SYN-B"),
    ("quote_basis.schema_version", "2"), ("quote_basis.scope", "international"),
    ("quote_basis.source.source_type", "unknown"),
    ("quote_basis.source.source_name", "https://private.test"),
    ("quote_basis.source.queried_at", "2026-09-09T08:00:00"),
    ("quote_basis.source.queried_at", "2026-09-10T08:00:00Z"),
    ("quote_basis.fees", [{"kind": "fuel", "amount": "1.001", "included_in_amount": "unknown"}]),
    ("quote_basis.fees", [{"kind": "fuel", "amount": "0.00", "included_in_amount": "unknown"}] * 2),
    ("quote_basis.internal", "private-canary"),
])
def test_server_and_eval_reject_malformed_basis(path, value):
    public = copy.deepcopy(public_quote())
    target = public
    keys = path.split(".")
    for name in keys[:-1]:
        target = target[name]
    target[keys[-1]] = value
    for model in (AgentResultResponse, AgentResultObservation):
        with pytest.raises(ValidationError):
            model.model_validate(public)


def test_public_quote_openapi_matches_both_runtime_schemas():
    path = Path(__file__).resolve().parents[3] / "docs/openapi/assistant-agent-v2.openapi.json"
    declared = json.loads(path.read_text())["components"]["schemas"]
    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if k != "title"}
        if isinstance(value, list):
            return [clean(v) for v in value]
        if isinstance(value, str):
            return value.replace("Observation", "Response")
        return value
    for model in (PostageQuoteBasisResponse, PostageQuoteBasisObservation):
        schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
        nested = schema.pop("$defs")
        assert clean(schema) == declared["PostageQuoteBasisResponse"]
        for name, value in nested.items():
            assert clean(value) == declared[name.replace("Observation", "Response")]
