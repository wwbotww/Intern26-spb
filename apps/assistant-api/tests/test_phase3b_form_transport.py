from __future__ import annotations

import asyncio
from urllib.parse import parse_qs

import httpx
import pytest

from spb_assistant_api.adapters.agent_http import AgentJsonHttpClient
from spb_assistant_api.domain.agent_errors import AgentOperationError
from spb_assistant_api.domain.failures import FailureCategory
from spb_assistant_api.observability.context import bind_request_id, reset_request_id


def _client(handler, **kwargs) -> AgentJsonHttpClient:
    return AgentJsonHttpClient(
        base_url="https://postal.example.test/api",
        timeout_seconds=kwargs.pop("timeout_seconds", 5),
        max_connections=2,
        verify_tls=True,
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def test_form_preserves_unicode_plus_and_json_with_exactly_one_encoding() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    fields = {"msgBody": '{"note":"北京 + &=/%"}', "dataDigest": "ab+/="}
    client = _client(handler)

    async def scenario() -> None:
        token = bind_request_id("postal-form-test")
        try:
            response = await client.request_json(
                capability="tracking", method="POST", path="interface",
                form_body=fields,
            )
            assert response.payload == {"ok": True}
        finally:
            reset_request_id(token)
            await client.close()

    asyncio.run(scenario())
    assert len(calls) == 1
    request = calls[0]
    assert request.url.query == b""
    assert request.url.path == "/api/interface"
    assert request.headers["content-type"] == (
        "application/x-www-form-urlencoded; charset=UTF-8"
    )
    assert request.headers["x-request-id"] == "postal-form-test"
    assert parse_qs(request.content.decode("ascii")) == {
        key: [value] for key, value in fields.items()
    }
    assert b"dataDigest=ab%2B%2F%3D" in request.content


@pytest.mark.parametrize(
    "kwargs",
    [
        {"json_body": {}, "form_body": {}},
        {"form_body": {}, "method": "GET"},
        {"form_body": {"field": 1}},
        {"form_body": {1: "field"}},
        {"form_body": {}, "headers": {"Content-Type": "application/json"}},
        {"form_body": {}, "headers": {
            "content-type": "application/x-www-form-urlencoded;charset=GBK"
        }},
    ],
)
def test_invalid_form_options_fail_before_network(kwargs) -> None:
    calls = []
    client = _client(lambda request: calls.append(request))

    async def scenario() -> None:
        try:
            with pytest.raises(ValueError):
                await client.request_json(
                    **{"capability": "tracking", "method": "POST",
                       "path": "interface", **kwargs}
                )
        finally:
            await client.close()

    asyncio.run(scenario())
    assert calls == []


@pytest.mark.parametrize(
    "content",
    [b'{"a":1,"a":2}', b'{"a":{"b":1,"b":2}}', b'{"a":NaN}',
     b'{"a":Infinity}', b'{"a":-Infinity}', b'\xff', b'not-json'],
)
def test_strict_json_rejects_ambiguous_and_nonstandard_payloads(content) -> None:
    client = _client(lambda request: httpx.Response(200, content=content))

    async def scenario() -> None:
        try:
            with pytest.raises(AgentOperationError) as raised:
                await client.request_json(
                    capability="tracking", method="POST", path="interface",
                    form_body={"msgBody": "{}"},
                )
            assert raised.value.failure.category is FailureCategory.CONTRACT_VIOLATION
            assert raised.value.failure.code == "upstream_json_invalid"
            assert raised.value.failure.retryable is False
            assert raised.value.__suppress_context__ is True
        finally:
            await client.close()

    asyncio.run(scenario())


def test_form_does_not_follow_redirects_or_forward_a_signed_body() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(307, headers={"Location": "https://other.example.test"})

    client = _client(handler)

    async def scenario() -> None:
        try:
            with pytest.raises(AgentOperationError) as raised:
                await client.request_json(
                    capability="tracking", method="POST", path="interface",
                    form_body={"msgBody": "{}", "dataDigest": "synthetic"},
                )
            assert raised.value.failure.retryable is False
        finally:
            await client.close()

    asyncio.run(scenario())
    assert len(calls) == 1


@pytest.mark.parametrize("stall_at", ["headers", "body"])
def test_total_http_deadline_bounds_stalled_headers_and_stream(stall_at) -> None:
    calls = []
    closed = []

    class StalledStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"ok":'
            await asyncio.Event().wait()

        async def aclose(self):
            closed.append(True)

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if stall_at == "headers":
            await asyncio.Event().wait()
        return httpx.Response(200, stream=StalledStream())

    client = _client(handler, timeout_seconds=0.02)

    async def scenario() -> None:
        try:
            with pytest.raises(AgentOperationError) as raised:
                await asyncio.wait_for(client.request_json(
                    capability="tracking", method="POST", path="interface",
                    form_body={"msgBody": "{}"},
                ), timeout=1)
            assert raised.value.failure.category is FailureCategory.UPSTREAM_TIMEOUT
            assert raised.value.failure.retryable is True
        finally:
            await client.close()

    asyncio.run(scenario())
    assert len(calls) == 1
    if stall_at == "body":
        assert closed == [True]


def test_http_client_does_not_load_ambient_proxy_settings(monkeypatch) -> None:
    # Use the real client constructor without making a request: MockTransport
    # itself disables environment proxies, which would mask a missing trust_env.
    monkeypatch.setenv("HTTPS_PROXY", "unsupported-proxy-scheme://fixture.invalid")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("SSL_CERT_FILE", "/nonexistent-synthetic-postal-ca.pem")
    client = AgentJsonHttpClient(
        base_url="https://postal.example.test/api",
        timeout_seconds=5,
        max_connections=2,
        verify_tls=True,
    )
    asyncio.run(client.close())
