from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from spb_assistant_api.adapters.agent_http import AgentJsonHttpClient
from spb_assistant_api.domain.agent_errors import AgentOperationError
from spb_assistant_api.domain.failures import AgentFailure, FailureCategory
from spb_assistant_api.observability.context import (
    bind_request_id,
    reset_request_id,
)
from spb_assistant_api.services.circuit_breaker import (
    CapabilityCircuitBreaker,
    CircuitState,
)
from spb_assistant_api.services.retry_schedule import RetrySchedule


def _client(
    handler,
    *,
    breaker: CapabilityCircuitBreaker | None = None,
    max_response_bytes: int = 1024,
) -> AgentJsonHttpClient:
    return AgentJsonHttpClient(
        base_url="https://shipping.example.test/api",
        timeout_seconds=5,
        max_connections=4,
        verify_tls=True,
        default_headers={"Authorization": "Bearer fixture-secret"},
        max_response_bytes=max_response_bytes,
        circuit_breaker=breaker,
        transport=httpx.MockTransport(handler),
    )


def test_http_client_propagates_context_without_exposing_wire_shape() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["authorization"] = request.headers.get("authorization")
        captured["request_id"] = request.headers.get("x-request-id")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"vendor_field": "opaque"})

    client = _client(handler)

    async def scenario() -> None:
        token = bind_request_id("phase3a-request")
        try:
            response = await client.request_json(
                capability="tracking",
                method="POST",
                path="v1/query",
                json_body={"opaque_request": "fixture"},
            )
            assert response.status_code == 200
            assert response.payload == {"vendor_field": "opaque"}
        finally:
            reset_request_id(token)
            await client.close()

    asyncio.run(scenario())

    assert captured == {
        "path": "/api/v1/query",
        "authorization": "Bearer fixture-secret",
        "request_id": "phase3a-request",
        "body": {"opaque_request": "fixture"},
    }


@pytest.mark.parametrize(
    ("case", "category", "code", "retryable"),
    [
        (
            "timeout",
            FailureCategory.UPSTREAM_TIMEOUT,
            "upstream_request_timeout",
            True,
        ),
        (
            "transport",
            FailureCategory.UPSTREAM_UNAVAILABLE,
            "upstream_transport_unavailable",
            True,
        ),
        (
            "rate_limit",
            FailureCategory.UPSTREAM_RATE_LIMITED,
            "upstream_rate_limited",
            True,
        ),
        (
            "server_error",
            FailureCategory.UPSTREAM_UNAVAILABLE,
            "upstream_server_error",
            True,
        ),
        (
            "http_timeout",
            FailureCategory.UPSTREAM_TIMEOUT,
            "upstream_http_timeout",
            True,
        ),
        (
            "invalid_json",
            FailureCategory.CONTRACT_VIOLATION,
            "upstream_json_invalid",
            False,
        ),
        (
            "unexpected_status",
            FailureCategory.CONTRACT_VIOLATION,
            "upstream_unexpected_http_status",
            False,
        ),
        (
            "unauthorized",
            FailureCategory.UPSTREAM_UNAVAILABLE,
            "upstream_unexpected_http_status",
            False,
        ),
        (
            "too_large",
            FailureCategory.CONTRACT_VIOLATION,
            "upstream_response_too_large",
            False,
        ),
    ],
)
def test_http_failures_map_to_stable_agent_taxonomy(
    case: str,
    category: FailureCategory,
    code: str,
    retryable: bool,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if case == "timeout":
            raise httpx.ReadTimeout("fixture timeout", request=request)
        if case == "transport":
            raise httpx.ConnectError("fixture unavailable", request=request)
        if case == "rate_limit":
            return httpx.Response(
                429,
                headers={"Retry-After": "2.5"},
                json={"code": "limited"},
            )
        if case == "server_error":
            return httpx.Response(503, json={"code": "down"})
        if case == "http_timeout":
            return httpx.Response(504, json={"code": "timeout"})
        if case == "invalid_json":
            return httpx.Response(200, content=b"not-json")
        if case == "unexpected_status":
            return httpx.Response(418, json={"code": "unexpected"})
        if case == "unauthorized":
            return httpx.Response(401, json={"code": "unauthorized"})
        return httpx.Response(200, json={"oversized": "payload"})

    client = _client(
        handler,
        breaker=CapabilityCircuitBreaker(failure_threshold=99),
        max_response_bytes=(4 if case == "too_large" else 1024),
    )

    async def scenario() -> AgentOperationError:
        try:
            with pytest.raises(AgentOperationError) as raised:
                await client.request_json(
                    capability="tracking",
                    method="GET",
                    path="v1/query",
                )
            return raised.value
        finally:
            await client.close()

    error = asyncio.run(scenario())

    assert error.failure.category is category
    assert error.failure.code == code
    assert error.failure.retryable is retryable
    if case == "rate_limit":
        assert error.failure.retry_after_seconds == 2.5


def test_circuit_breaker_isolates_capabilities_and_recovers_with_probe() -> None:
    current = [0.0]
    tracking_healthy = [False]
    calls = {"tracking": 0, "delivery_time": 0}
    breaker = CapabilityCircuitBreaker(
        failure_threshold=2,
        recovery_timeout_seconds=10,
        clock=lambda: current[0],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        capability = request.url.path.rsplit("/", 1)[-1]
        calls[capability] += 1
        if capability == "tracking" and not tracking_healthy[0]:
            return httpx.Response(503, json={"code": "down"})
        return httpx.Response(200, json={"status": "ok"})

    client = _client(handler, breaker=breaker)

    async def scenario() -> None:
        for _ in range(2):
            with pytest.raises(AgentOperationError) as raised:
                await client.request_json(
                    capability="tracking",
                    method="GET",
                    path="v1/tracking",
                )
            assert raised.value.failure.code == "upstream_server_error"

        with pytest.raises(AgentOperationError) as opened:
            await client.request_json(
                capability="tracking",
                method="GET",
                path="v1/tracking",
            )
        assert opened.value.failure.code == "capability_circuit_open"
        assert calls["tracking"] == 2

        delivery = await client.request_json(
            capability="delivery_time",
            method="GET",
            path="v1/delivery_time",
        )
        assert delivery.payload == {"status": "ok"}
        assert calls["delivery_time"] == 1

        current[0] = 11.0
        tracking_healthy[0] = True
        recovered = await client.request_json(
            capability="tracking",
            method="GET",
            path="v1/tracking",
        )
        assert recovered.payload == {"status": "ok"}
        assert (await breaker.snapshot("tracking")).state is (
            CircuitState.CLOSED
        )
        await client.close()

    asyncio.run(scenario())


def test_cancelled_half_open_probe_does_not_strand_circuit() -> None:
    current = [0.0]
    calls = [0]
    probe_started = asyncio.Event()
    wait_forever = asyncio.Event()
    breaker = CapabilityCircuitBreaker(
        failure_threshold=1,
        recovery_timeout_seconds=10,
        clock=lambda: current[0],
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        calls[0] += 1
        if calls[0] == 1:
            return httpx.Response(503, json={"code": "down"})
        if calls[0] == 2:
            probe_started.set()
            await wait_forever.wait()
        return httpx.Response(200, json={"status": "ok"})

    client = _client(handler, breaker=breaker)

    async def scenario() -> None:
        try:
            with pytest.raises(AgentOperationError):
                await client.request_json(
                    capability="tracking",
                    method="GET",
                    path="v1/tracking",
                )
            current[0] = 11.0
            probe = asyncio.create_task(
                client.request_json(
                    capability="tracking",
                    method="GET",
                    path="v1/tracking",
                )
            )
            await probe_started.wait()
            probe.cancel()
            with pytest.raises(asyncio.CancelledError):
                await probe

            assert (await breaker.snapshot("tracking")).state is (
                CircuitState.HALF_OPEN
            )
            recovered = await client.request_json(
                capability="tracking",
                method="GET",
                path="v1/tracking",
            )
            assert recovered.payload == {"status": "ok"}
        finally:
            await client.close()

    asyncio.run(scenario())


def test_http_client_rejects_credential_urls_and_absolute_request_paths() -> None:
    with pytest.raises(ValueError, match="base_url"):
        AgentJsonHttpClient(
            base_url="https://user:secret@example.test",
            timeout_seconds=5,
            max_connections=1,
            verify_tls=True,
        )

    client = _client(lambda request: httpx.Response(200, json={}))

    async def scenario() -> None:
        try:
            for path in (
                "https://attacker.example/path",
                "/v1/query",
                "v1/%2e%2e/private",
            ):
                with pytest.raises(ValueError, match="path"):
                    await client.request_json(
                        capability="tracking",
                        method="GET",
                        path=path,
                    )
        finally:
            await client.close()

    asyncio.run(scenario())


def test_retry_schedule_is_bounded_reproducible_and_honors_retry_after() -> None:
    schedule = RetrySchedule(
        base_delay_seconds=0.1,
        max_delay_seconds=2,
        jitter_ratio=0.2,
    )
    transient = AgentFailure(
        category=FailureCategory.UPSTREAM_TIMEOUT,
        code="fixture_timeout",
        message="fixture timeout",
        retryable=True,
    )
    limited = transient.model_copy(
        update={"retry_after_seconds": 1.5}
    )
    too_long = transient.model_copy(
        update={"retry_after_seconds": 30.0}
    )

    first = schedule.delay_seconds(
        failure=transient,
        retry_number=1,
        jitter_key="conversation-a",
    )
    repeated = schedule.delay_seconds(
        failure=transient,
        retry_number=1,
        jitter_key="conversation-a",
    )

    assert first == repeated
    assert first is not None and 0.08 <= first <= 0.12
    assert schedule.delay_seconds(
        failure=limited,
        retry_number=1,
        jitter_key="conversation-a",
    ) == 1.5
    assert schedule.delay_seconds(
        failure=too_long,
        retry_number=1,
        jitter_key="conversation-a",
    ) is None
