from __future__ import annotations

import asyncio
import json
import logging
import traceback
from urllib.parse import quote

import httpx
import pytest
from pydantic import SecretStr

from spb_assistant_api.adapters.agent_http import AgentJsonHttpClient
from spb_assistant_api.adapters.postal_postage import PostalPostageGateway
from spb_assistant_api.adapters.postal_postage_contract import CsbPostageSigner, PostalPostageProfile, PostalPostageSigner
from spb_assistant_api.domain.agent_errors import AgentOperationError
from spb_assistant_api.domain.failures import FailureCategory
from spb_assistant_api.tools.postage import PostageTool

from .postage_p1_fixture import NOW, command, preflight
from .postage_p2_fixture import FIXTURES, PRIVATE, bound_policy, config, fixture, gateway, prepared, request_form, with_quote


def observed(payload, *, cfg=None):
    async def run():
        client = gateway(lambda r: httpx.Response(200, json=payload), cfg=cfg)
        try:
            return await client.quote(prepared(cfg))
        finally:
            await client.close()
    return asyncio.run(run())


def test_fixture_evidence_is_explicitly_synthetic():
    manifest = fixture("manifest.json")
    assert manifest["provider_verified"] is False and manifest["live_requests"] == 0
    for name in manifest["cases"]:
        assert (FIXTURES / name).is_file()
    assert (FIXTURES / manifest["catalog"]).is_file()


@pytest.mark.parametrize("vector", fixture("signature_vectors.json"))
def test_two_independently_frozen_local_signature_vectors(vector):
    business = PostalPostageSigner(SecretStr(vector["password"]))
    csb = CsbPostageSigner(SecretStr(vector["access_key"]), SecretStr(vector["secret_key"]))
    assert business.sign(vector["form"]["map"]) == vector["business_signature"]
    assert business.sign(quote(vector["form"]["map"], safe="")) != vector["business_signature"]
    args = dict(api_name="getAllFeeForInterface", api_version="1.0.0", timestamp_ms=vector["timestamp_ms"])
    headers = csb.headers(vector["form"], **args)
    assert headers["_api_signature"] == vector["csb_signature"]
    assert csb.headers(dict(reversed(list(vector["form"].items()))), **args) == headers
    changed = {**vector["form"], "map": vector["form"]["map"] + " "}
    assert csb.headers(changed, **args)["_api_signature"] != vector["csb_signature"]
    for secret in ("password", "access_key", "secret_key"):
        assert vector[secret] not in repr(business) + repr(csb)


@pytest.mark.parametrize("vector", fixture("signature_vectors.json"))
def test_form_encoding_round_trip_keeps_signed_strings(vector):
    calls = []
    async def run():
        client = AgentJsonHttpClient(
            base_url="https://postage.invalid", timeout_seconds=1, max_connections=1,
            verify_tls=True, transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200, json={})),
        )
        try:
            await client.request_json(capability="postage", method="POST", path="CSB", form_body=vector["form"])
        finally:
            await client.close()
    asyncio.run(run())
    assert request_form(calls[0]) == vector["form"]
    assert calls[0].url.query == b""
    assert b"%2B" in calls[0].content  # Plus in signatures is escaped, not a space.


@pytest.mark.parametrize("changes", [
    {"password": ""}, {"password": " \t"}, {"access_key": "ak\r\nbad"},
    {"access_key": "中文"}, {"secret_key": "\ud800"}, {"sys_code": "TOO-LONG-CODE"},
    {"timeout_seconds": 0}, {"timeout_seconds": float("nan")},
    {"timeout_seconds": float("inf")}, {"max_response_bytes": 0},
    {"base_url": "https://real-provider.test"}, {"enabled": True, "mode": "live"},
])
def test_invalid_or_live_configuration_is_rejected(changes):
    with pytest.raises(ValueError):
        config(**changes)


@pytest.mark.parametrize("changes", [
    {"evidence": "reviewed_contract"}, {"business_signature": "hex-md5"},
    {"response_shape": "auto"}, {"response_weight_unit": "kg"},
    {"amount_unit": "minor"}, {"timezone": "Bad/Zone"}, {"currency": "CNY\n"},
])
def test_profile_does_not_probe_variants(changes):
    with pytest.raises(ValueError):
        PostalPostageProfile.model_validate({**fixture("profile.json"), **changes})


def test_live_transport_unbound_policy_and_pricing_mismatch_are_rejected():
    cfg = config()
    with pytest.raises(ValueError, match="MockTransport"):
        PostalPostageGateway(cfg, preflight=bound_policy(), transport=None)
    with pytest.raises(ValueError, match="bound"):
        gateway(lambda r: None, policy=preflight())
    with pytest.raises(ValueError, match="semantics"):
        cfg.profile.bind(preflight(currency="USD").catalog)
    with pytest.raises(ValueError, match="synthetic"):
        cfg.profile.bind(preflight(evidence="reviewed_contract").catalog)


def test_profile_drift_and_key_rotation_have_different_context_semantics():
    first = bound_policy()
    changed_cfg = config(profile={**fixture("profile.json"), "api_version": "1.0.1"})
    assert first.fingerprint != bound_policy(changed_cfg).fingerprint
    assert first.fingerprint == bound_policy(config(secret_key="rotated-synthetic-key")).fingerprint
    with pytest.raises(AgentOperationError):
        bound_policy(changed_cfg).validate_command(first.prepare(command()))


def test_gateway_request_projection_and_log_privacy(caplog):
    calls = []
    async def run():
        cfg = config()
        client = gateway(lambda r: calls.append(r) or httpx.Response(200, json=fixture()))
        try:
            result = await PostageTool(client, preflight=bound_policy()).execute(prepared())
            assert result.data.amount.as_tuple().exponent == -2
            assert result.data.billable_weight.value == 1500
            assert result.data.billable_weight.unit == "g"
            assert result.data.quote_basis.fees[0].included_in_amount == "unknown"
            assert str(result.data.amount) == "12.30"  # No addition of fuel.
            assert result.provenance[0].source_type == "fake_gateway"
            assert result.provenance[0].queried_at == NOW
            assert "合成" in result.warnings[0]
            assert PRIVATE not in result.model_dump_json()
            for field in ("password", "access_key", "secret_key"):
                assert getattr(cfg, field).get_secret_value() not in repr(cfg)
        finally:
            await client.close()
    with caplog.at_level(logging.DEBUG):
        asyncio.run(run())
    vector = fixture("signature_vectors.json")[0]
    assert len(calls) == 1 and calls[0].method == "POST"
    assert request_form(calls[0]) == vector["form"]
    assert calls[0].headers["_api_signature"] == vector["csb_signature"]
    assert calls[0].headers["_api_timestamp"] == str(vector["timestamp_ms"])
    assert calls[0].headers["_api_name"] == "getAllFeeForInterface"
    assert "messageHeader" not in calls[0].headers
    for private in (PRIVATE, vector["password"], vector["secret_key"], vector["access_key"], vector["business_signature"], vector["csb_signature"]):
        assert private not in caplog.text


@pytest.mark.parametrize("field,value", [
    ("totalFee", None), ("totalFee", ""), ("totalFee", 12.3), ("totalFee", True),
    ("totalFee", "-1"), ("totalFee", "1.001"), ("totalFee", "NaN"),
    ("totalFee", "Infinity"), ("totalFee", "1e2"), ("totalFee", " 12.30 "),
    ("totalFee", "1,000"), ("totalFee", "1000000000"), ("totalFee", "12.300"),
    ("standardFee", "nonsense"), ("realFee", ""), ("FJF", "-1"), ("FJF", "NaN"),
    ("weight", None), ("weight", "1251"), ("feeWeight", None), ("feeWeight", ""),
    ("feeWeight", "0"), ("feeWeight", "-1"), ("feeWeight", "1.5"), ("feeWeight", 1500),
    ("feeWeight", "1e5"), ("feeWeight", "1000001"), ("errorcode", ""),
    ("errorcode", "0000"), ("errorcode", 0), ("errorname", PRIVATE),
    ("volWeight", "100"), ("volRatio", "1.5"), ("volRatio", "NaN"),
    ("BXF", "1.00"), ("BJF", "1.00"), ("regFee", "1.00"), ("HZF", "1.00"),
])
def test_invalid_success_never_falls_back_to_another_amount_or_input_weight(field, value):
    with pytest.raises(AgentOperationError) as caught:
        observed(with_quote(**{field: value}))
    assert caught.value.failure.category is FailureCategory.CONTRACT_VIOLATION
    assert caught.value.failure.retryable is False
    assert PRIVATE not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("field", ["weight", "feeWeight", "errorcode", "totalFee"])
def test_required_success_fields_cannot_be_omitted(field):
    payload = fixture()
    decoded = json.loads(payload["body"]["retBody"])
    decoded["map"].pop(field)
    payload["body"]["retBody"] = json.dumps(decoded)
    with pytest.raises(AgentOperationError):
        observed(payload)


@pytest.mark.parametrize("shape", ["object", "double_encoded", "flat", "list", "null", "duplicate", "nonfinite"])
def test_ret_body_is_decoded_exactly_once(shape):
    payload = fixture()
    decoded = json.loads(payload["body"]["retBody"])
    payload["body"]["retBody"] = {
        "object": decoded, "double_encoded": json.dumps(payload["body"]["retBody"]),
        "flat": json.dumps(decoded["map"]), "list": "[]", "null": "null",
        "duplicate": '{"map":{},"map":{}}', "nonfinite": '{"map":{"errorcode":null,"totalFee":NaN}}',
    }[shape]
    with pytest.raises(AgentOperationError):
        observed(payload)


@pytest.mark.parametrize("layer,field,value", [
    ("root", "Code", 200), ("root", "Code", None), ("root", "body", "{}"),
    ("body", "retCode", 0), ("body", "retCode", None),
    ("body", "serialNo", "wrong"), ("body", "serialNo", None),
    ("body", "retDate", "2026-02-30 16:00:00"),
])
def test_envelope_types_date_and_request_correlation(layer, field, value):
    payload = fixture()
    (payload if layer == "root" else payload["body"])[field] = value
    with pytest.raises(AgentOperationError):
        observed(payload)


@pytest.mark.parametrize("code,reason", [
    ("500", "csb_internal"), ("501", "csb_not_authorized"), ("502", "csb_signature_rejected"),
    ("504", "csb_api_missing"), ("505", "csb_credentials_missing"), ("506", "csb_signature_missing"),
    ("507", "csb_parameters_missing"), ("508", "csb_secure_channel_required"),
    ("509", "csb_timestamp_missing"), ("510", "csb_timestamp_rejected"),
    ("800", "csb_protocol_failure"), ("801", "csb_provider_unreachable"), ("999", "csb_unknown_failure"),
])
def test_csb_gate_precedes_business_success_and_is_not_http_status(code, reason):
    with pytest.raises(AgentOperationError) as caught:
        observed({**fixture(), "Code": code})
    assert caught.value.failure.code == "postal_postage_" + reason
    assert caught.value.failure.retryable is False  # Private deployment retry contract is unconfirmed.


@pytest.mark.parametrize("code", ["001", "002", "010", "020", "099", "888"])
def test_business_gate_precedes_valid_price_and_ignores_raw_message(code):
    payload = fixture()
    payload["body"]["retCode"] = code
    with pytest.raises(AgentOperationError) as caught:
        observed(payload)
    assert caught.value.failure.code.startswith("postal_postage_business_")
    assert caught.value.failure.retryable is False
    assert PRIVATE not in str(caught.value.failure)


@pytest.mark.parametrize("amount", ["0", "0.00"])
def test_explicit_zero_is_not_missing(amount):
    assert observed(with_quote(totalFee=amount)).data.amount == 0


@pytest.mark.parametrize("kind,field,expected", [("standard", "standardFee", "11.50"), ("customer", "realFee", "9.00")])
def test_display_field_is_explicit_not_a_fallback_chain(kind, field, expected):
    cfg = config(profile={**fixture("profile.json"), "amount_kind": kind})
    policy = bound_policy(cfg, amount_kind=kind)
    client = gateway(lambda r: httpx.Response(200, json=with_quote(**{field: expected})), cfg=cfg, policy=policy)
    async def run():
        try:
            return await client.quote(policy.prepare(command()))
        finally:
            await client.close()
    assert str(asyncio.run(run()).data.amount) == expected


def test_disabled_adapter_does_not_read_environment_or_call(monkeypatch):
    monkeypatch.setenv("ASSISTANT_POSTAGE_ENABLED", "true")
    raw = config().model_dump()
    raw.pop("enabled")
    from spb_assistant_api.adapters.postal_postage_contract import PostalPostageConfig
    cfg = PostalPostageConfig.model_validate(raw)
    calls = []
    client = gateway(lambda r: calls.append(r), cfg=cfg)
    async def run():
        try:
            assert await client.readiness() == "disabled"
            with pytest.raises(AgentOperationError) as caught:
                await client.quote(prepared())
            assert caught.value.failure.code == "postal_postage_disabled"
        finally:
            await client.close()
        assert await client.readiness() == "not_ready"
    asyncio.run(run())
    assert calls == []
