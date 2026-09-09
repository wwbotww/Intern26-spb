from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from spb_assistant_api.adapters.postal_tracking import PostalTrackingGateway
from spb_assistant_api.adapters.postal_tracking_contract import PostalTrackingConfig
from spb_assistant_api.domain.agent_errors import AgentOperationError
from spb_assistant_api.domain.commands import TrackingCommand
from spb_assistant_api.services.circuit_breaker import CapabilityCircuitBreaker, CircuitState


MAIL = "1234567890123"
COMMAND = TrackingCommand(mail_no=MAIL)
FIXTURES = Path(__file__).parent / "fixtures" / "postal_tracking"


def payload():
    return json.loads((FIXTURES / "success.json").read_text(encoding="utf-8"))


def gateway(handler, breaker, **kwargs):
    return PostalTrackingGateway(
        PostalTrackingConfig(
            enabled=True, base_url="https://postal.example.test",
            send_id="SYNTHETIC", msg_kind="SYNTHETIC_TRACE",
            signing_system_id="synthetic-secret",
            expected_response_receive_id="SYNTHETIC", timezone="Asia/Shanghai",
        ),
        transport=httpx.MockTransport(handler), circuit_breaker=breaker, **kwargs,
    )


def fault(kind, request):
    body = payload()
    if kind == "timeout":
        raise httpx.ReadTimeout("synthetic private canary", request=request)
    if kind == "transport":
        raise httpx.ConnectError("synthetic private canary", request=request)
    if kind in {"429", "503", "401"}:
        return httpx.Response(int(kind), text="synthetic private canary")
    if kind == "json":
        return httpx.Response(200, text="{synthetic private canary")
    if kind == "schema":
        body["responseState"] = "true"
    elif kind == "recipient":
        body["receiveID"] = "WRONG"
    elif kind == "business":
        body.update(responseState=False, errorDesc="synthetic private canary")
    elif kind == "mail":
        body["responseItems"][0]["traceNo"] = "9999999999999"
    elif kind == "time":
        body["responseItems"][0]["opTime"] = "2026-02-30 09:00:00"
    return httpx.Response(200, json=body)


@pytest.mark.parametrize("kind", [
    "timeout", "transport", "429", "503", "401", "json", "schema",
    "recipient", "business", "mail", "time",
])
def test_one_failure_per_attempt_including_http_200_contract_errors(kind):
    now = [0.0]
    calls = []
    healthy = [False]
    breaker = CapabilityCircuitBreaker(clock=lambda: now[0])

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=payload()) if healthy[0] else fault(kind, request)

    async def scenario():
        client = gateway(handler, breaker)
        try:
            for count in range(1, 4):
                with pytest.raises(AgentOperationError) as caught:
                    await client.query(COMMAND)
                assert caught.value.failure.code != "capability_circuit_open"
                assert "private canary" not in str(caught.value.failure)
                assert (await breaker.snapshot("tracking")).consecutive_failures == count
            assert await client.readiness() == "degraded"
            with pytest.raises(AgentOperationError) as caught:
                await client.query(COMMAND)
            assert caught.value.failure.code == "capability_circuit_open"
            assert len(calls) == 3
            assert (await breaker.snapshot("tracking")).consecutive_failures == 3
            assert (await breaker.snapshot("postage")).state is CircuitState.CLOSED
            now[0] = 31
            healthy[0] = True
            assert (await client.query(COMMAND)).data.mail_no == MAIL
            assert await client.readiness() == "ready"
            assert (await breaker.snapshot("tracking")).consecutive_failures == 0
        finally:
            await client.close()

    asyncio.run(scenario())


def test_valid_no_match_is_a_contract_success_and_resets_failures():
    healthy = [False]
    breaker = CapabilityCircuitBreaker()

    def handler(request):
        body = payload()
        body["responseItems"] = []
        return httpx.Response(200, json=body) if healthy[0] else fault("schema", request)

    async def scenario():
        client = gateway(handler, breaker)
        try:
            with pytest.raises(AgentOperationError):
                await client.query(COMMAND)
            healthy[0] = True
            result = await client.query(COMMAND)
            assert result.data is None and result.source.source_type == "external_api"
            assert (await breaker.snapshot("tracking")).consecutive_failures == 0
        finally:
            await client.close()

    asyncio.run(scenario())


def test_cancelled_half_open_probe_releases_admission_without_counting_failure():
    async def scenario():
        now = [0.0]
        breaker = CapabilityCircuitBreaker(failure_threshold=1, clock=lambda: now[0])
        await breaker.record_failure("tracking")
        now[0] = 31
        started = asyncio.Event()
        block = [True]

        async def handler(request):
            if block[0]:
                started.set()
                await asyncio.Event().wait()
            return httpx.Response(200, json=payload())

        client = gateway(handler, breaker)
        try:
            task = asyncio.create_task(client.query(COMMAND))
            await asyncio.wait_for(started.wait(), 1)
            with pytest.raises(AgentOperationError) as caught:
                await client.query(COMMAND)
            assert caught.value.failure.code == "capability_circuit_open"
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert (await breaker.snapshot("tracking")).consecutive_failures == 1
            block[0] = False
            assert (await client.query(COMMAND)).data is not None
            assert await client.readiness() == "ready"
        finally:
            await client.close()

    asyncio.run(scenario())


def test_local_signing_setup_failure_is_not_an_upstream_observation():
    async def scenario():
        breaker = CapabilityCircuitBreaker()
        calls = []
        client = gateway(lambda request: calls.append(request), breaker, serial_number_factory=lambda: "bad serial")
        try:
            with pytest.raises(AgentOperationError) as caught:
                await client.query(COMMAND)
            assert caught.value.failure.code == "postal_tracking_serial_invalid"
            assert (await breaker.snapshot("tracking")).consecutive_failures == 0
            assert calls == []
        finally:
            await client.close()

    asyncio.run(scenario())


def test_local_clock_fault_after_response_is_not_counted_as_provider_failure():
    async def scenario():
        breaker = CapabilityCircuitBreaker()
        times = iter([datetime(2026, 9, 9, tzinfo=UTC), datetime(2026, 9, 9)])
        client = gateway(lambda request: httpx.Response(200, json=payload()), breaker, clock=lambda: next(times))
        try:
            with pytest.raises(AgentOperationError) as caught:
                await client.query(COMMAND)
            assert caught.value.failure.code == "postal_tracking_clock_invalid"
            assert (await breaker.snapshot("tracking")).consecutive_failures == 0
        finally:
            await client.close()

    asyncio.run(scenario())
