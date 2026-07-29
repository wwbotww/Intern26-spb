from __future__ import annotations

import hashlib
import hmac
import logging
import re
from dataclasses import dataclass
from time import perf_counter
from typing import Any
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..observability.context import bind_request_id, reset_request_id
from ..observability.metrics import ServiceMetrics
from ..security.rate_limit import SlidingWindowRateLimiter


logger = logging.getLogger(__name__)
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
EXEMPT_PATHS = frozenset(
    {"/health/live", "/health/ready", "/metrics"}
)


@dataclass(frozen=True)
class OperationsConfig:
    auth_enabled: bool
    api_keys: tuple[str, ...]
    rate_limit_enabled: bool
    rate_limit_requests: int
    rate_limit_window_seconds: int
    max_request_body_bytes: int


class OperationsMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        config: OperationsConfig,
        metrics: ServiceMetrics,
    ) -> None:
        self._app = app
        self._config = config
        self._metrics = metrics
        self._limiter = SlidingWindowRateLimiter(
            limit=config.rate_limit_requests,
            window_seconds=config.rate_limit_window_seconds,
        )

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        supplied_request_id = headers.get("x-request-id", "")
        request_id = (
            supplied_request_id
            if REQUEST_ID_RE.fullmatch(supplied_request_id)
            else uuid4().hex
        )
        token = bind_request_id(request_id)
        started = perf_counter()
        status_code = 500
        rate_headers: dict[str, str] = {}
        path = scope.get("path", "")
        method = scope.get("method", "")
        state: dict[str, Any] = scope.setdefault("state", {})
        self._metrics.in_flight.inc()

        async def send_with_headers(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                response_headers = MutableHeaders(scope=message)
                response_headers["X-Request-ID"] = request_id
                for name, value in rate_headers.items():
                    response_headers[name] = value
            await send(message)

        try:
            content_length = headers.get("content-length")
            if (
                content_length
                and content_length.isdigit()
                and int(content_length)
                > self._config.max_request_body_bytes
            ):
                await self._respond(
                    scope,
                    receive,
                    send_with_headers,
                    status_code=413,
                    code="request_too_large",
                    message="请求体超过服务上限",
                )
                return

            client_id = self._client_ip(scope)
            if path not in EXEMPT_PATHS and self._config.auth_enabled:
                if not self._config.api_keys:
                    await self._respond(
                        scope,
                        receive,
                        send_with_headers,
                        status_code=503,
                        code="auth_not_configured",
                        message="服务鉴权尚未配置",
                    )
                    return
                api_key = self._extract_api_key(headers)
                if not self._is_valid_key(api_key):
                    self._metrics.auth_failures.inc()
                    await self._respond(
                        scope,
                        receive,
                        send_with_headers,
                        status_code=401,
                        code="unauthorized",
                        message="缺少或无效的 API Key",
                        extra_headers={
                            "WWW-Authenticate": "Bearer",
                        },
                    )
                    return
                client_id = hashlib.sha256(
                    api_key.encode("utf-8")
                ).hexdigest()[:16]

            if (
                path not in EXEMPT_PATHS
                and self._config.rate_limit_enabled
            ):
                result = await self._limiter.check(client_id)
                rate_headers.update(
                    {
                        "X-RateLimit-Limit": str(
                            self._config.rate_limit_requests
                        ),
                        "X-RateLimit-Remaining": str(result.remaining),
                    }
                )
                if not result.allowed:
                    self._metrics.rate_limit_rejections.inc()
                    rate_headers["Retry-After"] = str(
                        result.retry_after
                    )
                    await self._respond(
                        scope,
                        receive,
                        send_with_headers,
                        status_code=429,
                        code="rate_limit_exceeded",
                        message="请求过于频繁，请稍后重试",
                    )
                    return

            state["request_id"] = request_id
            state["client_id"] = client_id
            await self._app(scope, receive, send_with_headers)
        finally:
            duration = perf_counter() - started
            route = getattr(scope.get("route"), "path", "__unmatched__")
            self._metrics.http_requests.labels(
                method=method,
                route=route,
                status=str(status_code),
            ).inc()
            self._metrics.http_duration.labels(
                method=method,
                route=route,
            ).observe(duration)
            self._metrics.in_flight.dec()
            logger.info(
                "request_completed",
                extra={
                    "method": method,
                    "path": path,
                    "route": route,
                    "status": status_code,
                    "duration_ms": round(duration * 1000, 3),
                    "client_id": state.get("client_id", "-"),
                },
            )
            reset_request_id(token)

    @staticmethod
    def _client_ip(scope: Scope) -> str:
        client = scope.get("client")
        return client[0] if client else "unknown"

    @staticmethod
    def _extract_api_key(headers: Headers) -> str:
        authorization = headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            return authorization[7:].strip()
        return headers.get("x-api-key", "").strip()

    def _is_valid_key(self, supplied: str) -> bool:
        if not supplied:
            return False
        supplied_bytes = supplied.encode("utf-8")
        matched = False
        for expected in self._config.api_keys:
            matched |= hmac.compare_digest(
                supplied_bytes,
                expected.encode("utf-8"),
            )
        return matched

    @staticmethod
    async def _respond(
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        status_code: int,
        code: str,
        message: str,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        response = JSONResponse(
            status_code=status_code,
            content={"detail": {"code": code, "message": message}},
            headers=extra_headers,
        )
        await response(scope, receive, send)
