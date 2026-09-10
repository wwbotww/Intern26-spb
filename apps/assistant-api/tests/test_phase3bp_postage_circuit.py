from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from spb_assistant_api.domain.agent_errors import AgentOperationError
from spb_assistant_api.domain.failures import FailureCategory
from spb_assistant_api.services.circuit_breaker import CapabilityCircuitBreaker, CircuitState

from .postage_p1_fixture import NOW, command, preflight
from .postage_p2_fixture import PRIVATE, config, fixture, gateway, prepared, response_for, with_quote


def fault(kind, request):
    if kind == "timeout":
        raise httpx.ReadTimeout(PRIVATE, request=request)
    if kind == "transport":
        raise httpx.ConnectError(PRIVATE, request=request)
    if kind in {"429", "504", "503", "401", "302"}:
        return httpx.Response(int(kind), headers={"Retry-After": "2", "Location": "https://elsewhere.invalid"}, text=PRIVATE)
    if kind == "json":
        return httpx.Response(200, text="{")
    if kind == "duplicate":
        return httpx.Response(200, text='{"Code":"500","Code":"200"}')
    payload = fixture()
    if kind == "csb":
        payload["Code"] = "504"
    elif kind == "business":
        payload["body"]["retCode"] = "020"
    elif kind == "serial":
        payload["body"]["serialNo"] = "wrong"
    elif kind == "amount":
        payload = with_quote(totalFee=None)
    elif kind == "unknown_quote":
        payload = with_quote(errorcode="9999", errorname=PRIVATE)
    return httpx.Response(200, json=payload)


@pytest.mark.parametrize("kind", [
    "timeout", "transport", "429", "504", "503", "401", "302", "json", "duplicate",
    "csb", "business", "serial", "amount", "unknown_quote",
])
def test_transport_and_semantic_faults_are_counted_once_per_attempt(kind):
    calls = []
    clock = [0.0]
    healthy = [False]
    breaker = CapabilityCircuitBreaker(clock=lambda: clock[0])
    def handler(request):
        calls.append(request)
        return response_for(request) if healthy[0] else fault(kind, request)
    async def run():
        client = gateway(handler, circuit_breaker=breaker)
        try:
            for count in range(1, 4):
                with pytest.raises(AgentOperationError) as caught:
                    await client.quote(prepared())
                assert PRIVATE not in str(caught.value.failure)
                assert (await breaker.snapshot("postage")).consecutive_failures == count
            with pytest.raises(AgentOperationError) as caught:
                await client.quote(prepared())
            assert caught.value.failure.code == "capability_circuit_open"
            assert len(calls) == 3  # No inner retry, no redirect, no variant probing.
            assert await client.readiness() == "degraded"
            assert (await breaker.snapshot("tracking")).state is CircuitState.CLOSED
            clock[0] = 31
            healthy[0] = True
            assert (await client.quote(prepared())).data.amount
            assert await client.readiness() == "ready"
            assert (await breaker.snapshot("postage")).consecutive_failures == 0
        finally:
            await client.close()
    asyncio.run(run())


@pytest.mark.parametrize("code,reason", [
    ("0001", "weight_rejected"), ("0002", "product_rejected"), ("0003", "manual_required"),
    ("0005", "customer_eligibility"), ("0006", "customer_eligibility"),
    ("0007", "customer_history_unavailable"), ("0008", "billing_zone_unavailable"),
    ("0009", "rate_unavailable"), ("0010", "discount_rejected"),
    ("0011", "billing_mode_unsupported"), ("0016", "collection_context_incomplete"),
])
def test_known_business_refusal_is_not_no_match_retry_or_circuit_failure(code, reason):
    clock = [0.0]
    breaker = CapabilityCircuitBreaker(failure_threshold=1, clock=lambda: clock[0])
    payload = fixture()
    payload["body"]["retBody"] = json.dumps({"map": {"errorcode": code, "errorname": PRIVATE}})
    async def run():
        await breaker.record_failure("postage")
        clock[0] = 31
        client = gateway(lambda r: httpx.Response(200, json=payload), circuit_breaker=breaker)
        try:
            with pytest.raises(AgentOperationError) as caught:
                await client.quote(prepared())
            assert caught.value.failure.code == "postage_quote_" + reason
            assert not caught.value.failure.retryable
            assert caught.value.failure.category is not FailureCategory.NO_MATCH
            assert (await breaker.snapshot("postage")).state is CircuitState.CLOSED
            assert (await breaker.snapshot("postage")).consecutive_failures == 0
        finally:
            await client.close()
    asyncio.run(run())


@pytest.mark.parametrize("status,category,retryable", [
    (504, FailureCategory.UPSTREAM_TIMEOUT, True),
    (429, FailureCategory.UPSTREAM_RATE_LIMITED, True),
    (401, FailureCategory.UPSTREAM_UNAVAILABLE, False),
])
def test_http_statuses_are_distinct_from_equal_numbered_csb_codes(status, category, retryable):
    async def run():
        client = gateway(lambda r: fault(str(status), r))
        try:
            with pytest.raises(AgentOperationError) as caught:
                await client.quote(prepared())
            assert caught.value.failure.category is category
            assert caught.value.failure.retryable is retryable
            if status == 429:
                assert caught.value.failure.retry_after_seconds == 2
        finally:
            await client.close()
    asyncio.run(run())


def test_cancellation_releases_half_open_probe_and_only_one_probe_is_admitted():
    async def run():
        clock = [0.0]
        breaker = CapabilityCircuitBreaker(failure_threshold=1, clock=lambda: clock[0])
        await breaker.record_failure("postage")
        clock[0] = 31
        entered = asyncio.Event()
        block = [True]
        calls = []
        async def handler(request):
            calls.append(request)
            if block[0]:
                entered.set()
                await asyncio.Event().wait()
            return response_for(request)
        client = gateway(handler, circuit_breaker=breaker)
        try:
            task = asyncio.create_task(client.quote(prepared()))
            await asyncio.wait_for(entered.wait(), timeout=1)
            with pytest.raises(AgentOperationError) as caught:
                await client.quote(prepared())
            assert caught.value.failure.code == "capability_circuit_open"
            assert len(calls) == 1
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert (await breaker.snapshot("postage")).consecutive_failures == 1
            block[0] = False
            await client.quote(prepared())
            assert (await breaker.snapshot("postage")).state is CircuitState.CLOSED
        finally:
            await client.close()
    asyncio.run(run())


@pytest.mark.parametrize("local_fault", ["clock", "serial", "signer", "unprepared", "stale"])
def test_local_faults_issue_no_request_or_circuit_accounting(local_fault, monkeypatch):
    from spb_assistant_api.adapters.postal_postage_contract import PostalPostageSigner
    breaker = CapabilityCircuitBreaker()
    calls = []
    kwargs = {}
    cmd = prepared()
    if local_fault == "clock":
        kwargs["clock"] = lambda: NOW.replace(tzinfo=None)
    elif local_fault == "serial":
        kwargs["serial_number_factory"] = lambda: "x" * 37
    elif local_fault == "signer":
        def blocked(*args):
            raise ValueError(PRIVATE)
        monkeypatch.setattr(PostalPostageSigner, "sign", blocked)
    elif local_fault == "unprepared":
        cmd = command()
    elif local_fault == "stale":
        cmd = preflight().prepare(command())
    async def run():
        client = gateway(lambda r: calls.append(r), circuit_breaker=breaker, **kwargs)
        try:
            with pytest.raises(AgentOperationError):
                await client.quote(cmd)
            assert calls == []
            assert (await breaker.snapshot("postage")).consecutive_failures == 0
        finally:
            await client.close()
    asyncio.run(run())


def test_response_size_bound_total_timeout_and_stream_cleanup():
    class SlowStream(httpx.AsyncByteStream):
        def __init__(self):
            self.closed = False
        async def __aiter__(self):
            yield b'{"Code":'
            await asyncio.Event().wait()
        async def aclose(self):
            self.closed = True
    async def run():
        small = gateway(lambda r: httpx.Response(200, json=fixture()), cfg=config(max_response_bytes=16))
        try:
            with pytest.raises(AgentOperationError) as caught:
                await small.quote(prepared())
            assert caught.value.failure.code == "upstream_response_too_large"
        finally:
            await small.close()
        stream = SlowStream()
        slow = gateway(lambda r: httpx.Response(200, stream=stream), cfg=config(timeout_seconds=0.01))
        try:
            with pytest.raises(AgentOperationError) as caught:
                await slow.quote(prepared())
            assert caught.value.failure.category is FailureCategory.UPSTREAM_TIMEOUT
            assert stream.closed
        finally:
            await slow.close()
    asyncio.run(run())
