from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, NoReturn
from urllib.parse import unquote, urlsplit

import httpx

from ..domain.agent_errors import AgentOperationError
from ..domain.failures import AgentFailure, FailureCategory
from ..observability.context import current_request_id
from ..services.circuit_breaker import CapabilityCircuitBreaker


HttpMethod = Literal["GET", "POST"]


@dataclass(frozen=True, slots=True)
class JsonHttpResponse:
    status_code: int
    payload: Any


class AgentJsonHttpClient:
    """Shared single-attempt transport for future read-only Agent gateways.

    The LangGraph workflow owns the retry budget. Keeping this layer to one
    network attempt prevents transport retries from multiplying graph retries.
    """

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        max_connections: int,
        verify_tls: bool,
        default_headers: Mapping[str, str] | None = None,
        max_response_bytes: int = 1_048_576,
        circuit_breaker: CapabilityCircuitBreaker | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        normalized_url = _validate_base_url(base_url)
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")
        if max_connections < 1:
            raise ValueError("max_connections 必须大于 0")
        if max_response_bytes < 1:
            raise ValueError("max_response_bytes 必须大于 0")
        self._default_headers = dict(default_headers or {})
        self._max_response_bytes = max_response_bytes
        self._breaker = circuit_breaker or CapabilityCircuitBreaker()
        self._client = httpx.AsyncClient(
            base_url=f"{normalized_url.rstrip('/')}/",
            timeout=httpx.Timeout(
                timeout_seconds,
                connect=min(timeout_seconds, 10.0),
            ),
            verify=verify_tls,
            follow_redirects=False,
            limits=httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=max_connections,
            ),
            transport=transport,
        )

    async def request_json(
        self,
        *,
        capability: str,
        method: HttpMethod,
        path: str,
        expected_statuses: frozenset[int] = frozenset({200}),
        json_body: Mapping[str, Any] | None = None,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> JsonHttpResponse:
        if method not in {"GET", "POST"}:
            raise ValueError("method 只允许 GET 或 POST")
        normalized_path = _validate_relative_path(path)
        if not expected_statuses or any(
            status < 100 or status > 599 for status in expected_statuses
        ):
            raise ValueError("expected_statuses 必须是有效 HTTP 状态集合")
        try:
            await self._breaker.before_call(capability)
            async with self._client.stream(
                method,
                normalized_path,
                json=dict(json_body) if json_body is not None else None,
                params=dict(params) if params is not None else None,
                headers=self._headers(headers),
            ) as response:
                await self._validate_status(
                    capability,
                    response,
                    expected_statuses=expected_statuses,
                )
                status_code = response.status_code
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(content) + len(chunk) > self._max_response_bytes:
                        await self._fail(
                            capability,
                            AgentFailure(
                                category=FailureCategory.CONTRACT_VIOLATION,
                                code="upstream_response_too_large",
                                message="上游响应超过大小上限",
                            ),
                        )
                    content.extend(chunk)
        except asyncio.CancelledError:
            await asyncio.shield(
                self._breaker.record_aborted(capability)
            )
            raise
        except httpx.TimeoutException as exc:
            await self._fail(
                capability,
                AgentFailure(
                    category=FailureCategory.UPSTREAM_TIMEOUT,
                    code="upstream_request_timeout",
                    message="上游请求超时",
                    retryable=True,
                ),
                cause=exc,
            )
        except httpx.TransportError as exc:
            await self._fail(
                capability,
                AgentFailure(
                    category=FailureCategory.UPSTREAM_UNAVAILABLE,
                    code="upstream_transport_unavailable",
                    message="无法连接上游服务",
                    retryable=True,
                ),
                cause=exc,
            )

        if not content:
            payload: Any = None
        else:
            try:
                payload = json.loads(content)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                await self._fail(
                    capability,
                    AgentFailure(
                        category=FailureCategory.CONTRACT_VIOLATION,
                        code="upstream_json_invalid",
                        message="上游响应不是有效 JSON",
                    ),
                    cause=exc,
                )
        await self._breaker.record_success(capability)
        return JsonHttpResponse(
            status_code=status_code,
            payload=payload,
        )

    async def _validate_status(
        self,
        capability: str,
        response: httpx.Response,
        *,
        expected_statuses: frozenset[int],
    ) -> None:
        if response.status_code == 429:
            await self._fail(
                capability,
                AgentFailure(
                    category=FailureCategory.UPSTREAM_RATE_LIMITED,
                    code="upstream_rate_limited",
                    message="上游服务已限流",
                    retryable=True,
                    retry_after_seconds=_retry_after_seconds(
                        response.headers
                    ),
                ),
            )
        if response.status_code in {408, 504}:
            await self._fail(
                capability,
                AgentFailure(
                    category=FailureCategory.UPSTREAM_TIMEOUT,
                    code="upstream_http_timeout",
                    message="上游服务返回超时状态",
                    retryable=True,
                ),
            )
        if response.status_code >= 500:
            await self._fail(
                capability,
                AgentFailure(
                    category=FailureCategory.UPSTREAM_UNAVAILABLE,
                    code="upstream_server_error",
                    message="上游服务执行失败",
                    retryable=True,
                ),
            )
        if response.status_code not in expected_statuses:
            category = (
                FailureCategory.UPSTREAM_UNAVAILABLE
                if response.status_code in {401, 403}
                else FailureCategory.CONTRACT_VIOLATION
            )
            await self._fail(
                capability,
                AgentFailure(
                    category=category,
                    code="upstream_unexpected_http_status",
                    message=(
                        "上游服务返回未在接口契约中的 HTTP 状态"
                    ),
                ),
            )

    def _headers(
        self,
        extra: Mapping[str, str] | None,
    ) -> dict[str, str]:
        headers = {"Accept": "application/json", **self._default_headers}
        headers.update(extra or {})
        request_id = current_request_id()
        has_request_id = any(
            name.lower() == "x-request-id" for name in headers
        )
        if request_id != "-" and not has_request_id:
            headers["X-Request-ID"] = request_id
        return headers

    async def _fail(
        self,
        capability: str,
        failure: AgentFailure,
        *,
        cause: BaseException | None = None,
    ) -> NoReturn:
        await self._breaker.record_failure(capability)
        error = AgentOperationError(failure)
        if cause is None:
            raise error
        raise error from cause

    async def close(self) -> None:
        await self._client.aclose()


def _validate_base_url(value: str) -> str:
    normalized = value.strip()
    parsed = urlsplit(normalized)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "base_url 必须是无凭证、查询参数和片段的 HTTP(S) URL"
        )
    return normalized


def _validate_relative_path(value: str) -> str:
    normalized = value.strip()
    parsed = urlsplit(normalized)
    decoded_parts = unquote(parsed.path).split("/")
    if (
        not normalized
        or normalized.startswith("/")
        or "\\" in normalized
        or parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or any(part in {".", ".."} for part in decoded_parts)
    ):
        raise ValueError("path 必须是无查询参数和片段的相对路径")
    return normalized


def _retry_after_seconds(headers: Mapping[str, str]) -> float | None:
    raw = headers.get("Retry-After", "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return min(value, 3600.0)
