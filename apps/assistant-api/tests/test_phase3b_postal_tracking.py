from __future__ import annotations

import asyncio
import base64
import copy
import json
import logging
import traceback
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, quote

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from spb_assistant_api.adapters.postal_tracking import PostalTrackingGateway
from spb_assistant_api.adapters.postal_tracking_contract import (
    POSTAL_TRACKING_PROFILE,
    PostalLegacySigner,
    PostalTrackingConfig,
    PostalTrackingRequest,
)
from spb_assistant_api.domain.agent_errors import AgentOperationError
from spb_assistant_api.domain.commands import TrackingCommand
from spb_assistant_api.domain.failures import FailureCategory
from spb_assistant_api.domain.intents import Intent
from spb_assistant_api.domain.results import AgentResult, AgentResultStatus
from spb_assistant_api.services.result_validator import AgentResultValidator


FIXTURES = Path(__file__).parent / "fixtures" / "postal_tracking"
MAIL_NO = "1234567890123"
NOW = datetime(2026, 9, 9, 2, tzinfo=UTC)


def _fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _config(**changes) -> PostalTrackingConfig:
    return PostalTrackingConfig(**{
        "enabled": True,
        "base_url": "https://postal.example.test/api",
        "send_id": "SYNTHETIC",
        "msg_kind": "SYNTHETIC_JDPT_TRACE",
        "signing_system_id": SecretStr("synthetic-system"),
        "expected_response_receive_id": "SYNTHETIC",
        "timezone": "Asia/Shanghai",
        **changes,
    })


def _gateway(handler, *, config=None, **kwargs) -> PostalTrackingGateway:
    return PostalTrackingGateway(
        config or _config(),
        transport=httpx.MockTransport(handler),
        clock=kwargs.pop("clock", lambda: NOW),
        serial_number_factory=kwargs.pop("serial_number_factory", lambda: "synth-1"),
        **kwargs,
    )


def _query(payload, *, config=None, mail_no=MAIL_NO):
    gateway = _gateway(
        lambda request: httpx.Response(200, json=payload), config=config
    )

    async def scenario():
        try:
            return (await gateway.query(TrackingCommand(mail_no=mail_no))).data
        finally:
            await gateway.close()

    return asyncio.run(scenario())


def test_fixture_provenance_is_synthetic_and_unverified() -> None:
    manifest = _fixture("manifest.json")
    assert manifest["profile"] == POSTAL_TRACKING_PROFILE
    assert manifest["classification"] == "synthetic-development"
    assert manifest["provider_verified"] is False
    for name in manifest["cases"]:
        assert (FIXTURES / name).is_file()


@pytest.mark.parametrize("vector", _fixture("signature_vectors.json"))
def test_legacy_signer_matches_frozen_local_not_provider_vectors(vector) -> None:
    signer = PostalLegacySigner(SecretStr(vector["signing_system_id"]))
    signature = signer.sign(vector["message_body"])
    assert signature == vector["expected_digest"]
    assert len(base64.b64decode(signature, validate=True)) == 16
    assert signer.sign(quote(vector["message_body"], safe="")) != signature
    assert vector["signing_system_id"] not in repr(signer)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("base_url", "http://postal.example.test"),
        ("base_url", "https://name:password@postal.example.test"),
        ("base_url", "https://postal.example.test/?key=value"),
        ("base_url", "https://postal.example.test/#fragment"),
        ("path", "https://other.example.test/interface"),
        ("path", "/interface"),
        ("path", "../interface"),
        ("path", "%2e%2e/interface"),
        ("path", "interface?sendID=other"),
        ("signing_system_id", ""),
        ("signing_system_id", "   "),
        ("send_id", "client\r\nInjected:value"),
        ("msg_kind", ""),
        ("expected_response_receive_id", ""),
        ("timezone", ""),
        ("timezone", "Not/AZone"),
        ("province_no", "999"),
        ("province_no", "９９"),
        ("province_no", 99),
        ("timeout_seconds", 0),
        ("timeout_seconds", float("nan")),
        ("max_response_bytes", 1_048_577),
    ],
)
def test_configuration_rejects_unsafe_or_undefined_inputs(field, value) -> None:
    with pytest.raises(ValidationError):
        _config(**{field: value})


def test_disabled_adapter_makes_no_request_and_does_not_read_env(monkeypatch) -> None:
    monkeypatch.setenv("ASSISTANT_POSTAL_TRACKING_ENABLED", "true")
    config_values = _config().model_dump()
    config_values.pop("enabled")
    config = PostalTrackingConfig(**config_values)
    assert config.enabled is False
    calls = []
    gateway = _gateway(lambda request: calls.append(request), config=config)

    async def scenario() -> None:
        try:
            with pytest.raises(AgentOperationError) as raised:
                await gateway.query(TrackingCommand(mail_no=MAIL_NO))
            assert raised.value.failure.code == "postal_tracking_disabled"
            assert raised.value.failure.retryable is False
        finally:
            await gateway.close()

    asyncio.run(scenario())
    assert calls == []


def test_postal_adapter_sends_document_form_and_validates_domain_projection(caplog) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_fixture("success.json"))

    gateway = _gateway(handler)
    command = TrackingCommand(mail_no=MAIL_NO)

    async def scenario():
        try:
            return (await gateway.query(command)).data
        finally:
            await gateway.close()

    with caplog.at_level(logging.DEBUG):
        data = asyncio.run(scenario())
    assert data is not None
    assert len(calls) == 1
    request = calls[0]
    assert request.method == "POST"
    assert request.url.path == "/api/interface"
    assert request.url.query == b""
    assert parse_qs(request.content.decode()) == {
        "sendID": ["SYNTHETIC"], "proviceNo": ["99"],
        "msgKind": ["SYNTHETIC_JDPT_TRACE"], "serialNo": ["synth-1"],
        "sendDate": ["20260909100000"], "receiveID": ["JDPT"],
        "dataType": ["1"], "dataDigest": ["AqD+kvbrUSBXKeqVTP8D3A=="],
        "msgBody": ['{"traceNo":"1234567890123"}'],
    }
    assert "msgBody" not in request.headers
    assert "dataDigest" not in request.headers
    assert data.mail_no == MAIL_NO
    assert data.current_status == "运输"
    assert data.queried_at == NOW
    assert [event.event_code for event in data.events] == ["203", "20"]
    assert data.events[0].occurred_at == datetime(2026, 9, 8, tzinfo=UTC)
    assert data.events[1].occurred_at == datetime(2026, 9, 9, 1, tzinfo=UTC)
    result = AgentResult(
        tool="tracking", intent=Intent.TRACKING, status=AgentResultStatus.SUCCESS,
        answer="合成轨迹校验", data=data,
    )
    AgentResultValidator().validate(command=command, result=result)
    serialized = data.model_dump_json()
    for private in ("operatorName", "operatorNo", "SYNTH-STAFF-1", "合成员工甲"):
        assert private not in serialized
    for private in (MAIL_NO, "synthetic-system", "AqD+kvbrUSBXKeqVTP8D3A=="):
        assert private not in caplog.text


def test_batch_field_is_only_sent_when_explicitly_configured() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(parse_qs(request.content.decode()))
        return httpx.Response(200, json=_fixture("empty.json"))

    gateway = _gateway(handler, config=_config(batch_no="synth-batch"))

    async def scenario() -> None:
        try:
            assert (await gateway.query(TrackingCommand(mail_no=MAIL_NO))).data is None
        finally:
            await gateway.close()

    asyncio.run(scenario())
    assert calls[0]["batchNo"] == ["synth-batch"]


@pytest.mark.parametrize("mail_no", ["0" * 13, "EA123456789CN", "A" * 30])
def test_adapter_preserves_supported_identifiers_without_narrowing_domain(mail_no) -> None:
    payload = _fixture("success.json")
    for item in payload["responseItems"]:
        item["traceNo"] = mail_no
    data = _query(payload, mail_no=mail_no)
    assert data is not None and data.mail_no == mail_no
    body = PostalTrackingRequest(traceNo=mail_no).message_body()
    assert json.loads(body)["traceNo"] == mail_no


def test_overlong_mail_number_is_rejected_before_network() -> None:
    calls = []
    gateway = _gateway(lambda request: calls.append(request))

    async def scenario() -> None:
        try:
            with pytest.raises(AgentOperationError) as raised:
                await gateway.query(TrackingCommand(mail_no="A" * 31))
            assert raised.value.failure.category is FailureCategory.INVALID_INPUT
            assert raised.value.failure.retryable is False
        finally:
            await gateway.close()

    asyncio.run(scenario())
    assert calls == []


def test_only_explicit_success_with_an_empty_list_means_no_records() -> None:
    assert _query(_fixture("empty.json")) is None


def test_business_failure_does_not_guess_from_error_text() -> None:
    with pytest.raises(AgentOperationError) as raised:
        _query(_fixture("business_rejected.json"))
    assert raised.value.failure.code == "postal_tracking_business_rejected"
    assert raised.value.failure.category is FailureCategory.UPSTREAM_UNAVAILABLE
    assert raised.value.failure.retryable is False
    assert "not found" not in str(raised.value)


@pytest.mark.parametrize(
    ("case", "code"),
    [
        ("cross_mail", "postal_tracking_mail_no_mismatch"),
        ("recipient", "postal_tracking_recipient_mismatch"),
        ("absent_items", "postal_tracking_items_missing"),
        ("null_items", "postal_tracking_items_missing"),
        ("root_list", "postal_tracking_response_invalid"),
        ("null_body", "postal_tracking_response_invalid"),
        ("generic_envelope", "postal_tracking_response_invalid"),
        ("string_boolean", "postal_tracking_response_invalid"),
        ("integer_boolean", "postal_tracking_response_invalid"),
        ("too_many_events", "postal_tracking_response_invalid"),
        ("missing_description", "postal_tracking_response_invalid"),
        ("blank_description", "postal_tracking_response_invalid"),
        ("too_long_description", "postal_tracking_response_invalid"),
        ("integer_code", "postal_tracking_response_invalid"),
        ("integer_mail_no", "postal_tracking_response_invalid"),
        ("iso_time", "postal_tracking_response_invalid"),
        ("unicode_time", "postal_tracking_response_invalid"),
        ("invalid_date", "postal_tracking_event_invalid"),
    ],
)
def test_response_drift_fails_closed_without_partial_facts(case, code) -> None:
    payload = _fixture("success.json")
    if case == "cross_mail":
        payload = _fixture("cross_mail.json")
    elif case == "recipient":
        payload["receiveID"] = "OTHER"
    elif case == "absent_items":
        del payload["responseItems"]
    elif case == "null_items":
        payload["responseItems"] = None
    elif case == "root_list":
        payload = [payload]
    elif case == "null_body":
        payload = None
    elif case == "generic_envelope":
        payload = {"msgBody": payload}
    elif case == "string_boolean":
        payload["responseState"] = "true"
    elif case == "integer_boolean":
        payload["responseState"] = 1
    elif case == "too_many_events":
        payload["responseItems"] = [payload["responseItems"][0]] * 31
    elif case == "missing_description":
        del payload["responseItems"][0]["opDesc"]
    else:
        field, value = {
            "blank_description": ("opDesc", "  "),
            "too_long_description": ("opDesc", "x" * 1001),
            "integer_code": ("opCode", 20),
            "integer_mail_no": ("traceNo", 1234567890123),
            "iso_time": ("opTime", "2026-09-09T09:00:00+08:00"),
            "unicode_time": ("opTime", "2026-09-09 ０９:00:00"),
            "invalid_date": ("opTime", "2026-02-30 09:00:00"),
        }[case]
        payload["responseItems"][0][field] = value
    with pytest.raises(AgentOperationError) as raised:
        _query(payload)
    assert raised.value.failure.category is FailureCategory.CONTRACT_VIOLATION
    assert raised.value.failure.code == code
    assert raised.value.failure.retryable is False


def test_unknown_operation_code_is_preserved_not_reinterpreted() -> None:
    payload = _fixture("success.json")
    payload["responseItems"][0].update(opCode="NEW_CODE", opName="供应方新事件")
    data = _query(payload)
    assert data is not None
    assert data.current_status == "供应方新事件"
    assert data.events[-1].event_code == "NEW_CODE"


def test_equal_time_conflicting_events_do_not_invent_a_final_status() -> None:
    payload = _fixture("success.json")
    payload["responseItems"][1]["opTime"] = payload["responseItems"][0]["opTime"]
    payload["responseItems"].append(copy.deepcopy(payload["responseItems"][0]))
    data = _query(payload)
    assert data is not None
    assert len(data.events) == 3
    assert data.current_status == "同一时刻有多条轨迹，请查看明细"
    assert [event.event_code for event in data.events] == ["20", "203", "20"]


@pytest.mark.parametrize("op_time", ["2026-03-08 02:30:00", "2026-11-01 01:30:00"])
def test_ambiguous_or_nonexistent_local_times_are_not_guessed(op_time) -> None:
    payload = _fixture("success.json")
    payload["responseItems"][0]["opTime"] = op_time
    with pytest.raises(AgentOperationError) as raised:
        _query(payload, config=_config(timezone="America/New_York"))
    assert raised.value.failure.code == "postal_tracking_event_invalid"


def test_unknown_fields_and_staff_details_cannot_escape_projection() -> None:
    payload = _fixture("success.json")
    payload["privatePayload"] = "private-envelope-canary"
    item = payload["responseItems"][0]
    item.update(
        operatorNo="SYNTH-STAFF-9", operatorName="合成员工乙",
        opDesc="合成员工乙 SYNTH-STAFF-9 联系 13800000000 staff@example.test",
        privateField="private-event-canary",
    )
    data = _query(payload)
    assert data is not None
    public = data.model_dump_json()
    for private in (
        "private-envelope-canary", "private-event-canary", "SYNTH-STAFF-9",
        "合成员工乙", "13800000000", "staff@example.test", "operatorName",
    ):
        assert private not in public
    assert "[已隐藏]" in public
    assert "[电话已隐藏]" in public


def test_validation_errors_do_not_expose_raw_payload_or_credentials() -> None:
    payload = _fixture("success.json")
    payload["responseItems"][0]["opDesc"] = {"private": "raw-private-canary"}
    with pytest.raises(AgentOperationError) as raised:
        _query(payload)
    formatted = "".join(traceback.format_exception(raised.value))
    assert "raw-private-canary" not in formatted
    assert "synthetic-system" not in repr(_config())
    assert "postal.example.test" not in repr(_config())


@pytest.mark.parametrize(
    ("status", "category", "retryable"),
    [
        (401, FailureCategory.UPSTREAM_UNAVAILABLE, False),
        (403, FailureCategory.UPSTREAM_UNAVAILABLE, False),
        (429, FailureCategory.UPSTREAM_RATE_LIMITED, True),
        (503, FailureCategory.UPSTREAM_UNAVAILABLE, True),
        (504, FailureCategory.UPSTREAM_TIMEOUT, True),
    ],
)
def test_http_failures_are_single_attempt_and_never_return_fixture_data(
    status, category, retryable
) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, json=_fixture("success.json"))

    gateway = _gateway(handler)

    async def scenario() -> None:
        try:
            with pytest.raises(AgentOperationError) as raised:
                await gateway.query(TrackingCommand(mail_no=MAIL_NO))
            assert raised.value.failure.category is category
            assert raised.value.failure.retryable is retryable
        finally:
            await gateway.close()

    asyncio.run(scenario())
    assert len(calls) == 1


@pytest.mark.parametrize("case", ["clock", "serial", "signer"])
def test_local_preflight_errors_make_no_request(case, monkeypatch) -> None:
    calls = []
    overrides = {}
    if case == "clock":
        overrides["clock"] = lambda: datetime(2026, 9, 9)
    elif case == "serial":
        overrides["serial_number_factory"] = lambda: "bad\r\nserial"
    else:
        def blocked_md5(*args):
            raise ValueError("platform policy prohibits MD5")

        monkeypatch.setattr(
            "spb_assistant_api.adapters.postal_tracking_contract.hashlib.md5",
            blocked_md5,
        )
    gateway = _gateway(lambda request: calls.append(request), **overrides)

    async def scenario() -> None:
        try:
            with pytest.raises(AgentOperationError) as raised:
                await gateway.query(TrackingCommand(mail_no=MAIL_NO))
            assert raised.value.failure.retryable is False
        finally:
            await gateway.close()

    asyncio.run(scenario())
    assert calls == []
