from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..domain.exceptions import (
    PolicySourceContractError,
    PolicySourceError,
    PolicySourceUnavailableError,
)
from ..domain.policy import PolicyCitation, PolicyQueryResult
from ..observability.context import current_request_id


logger = logging.getLogger(__name__)


class RagCitationPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    index: int = Field(ge=1)
    chunk_id: str
    document_id: str
    title: str
    source_url: str
    document_no: str = ""
    published_at: str = ""
    source_org: str = ""
    section_path: str = ""
    score: float
    rerank_score: float | None = None
    excerpt: str


class RagChatPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    answer: str
    citations: list[RagCitationPayload]
    usage: dict[str, Any] = Field(default_factory=dict)
    finish_reason: str


class RagPolicyClient:
    """HTTP-only adapter for the existing policy RAG service."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float,
        health_timeout_seconds: float,
        top_k: int,
        candidate_k: int,
        max_connections: int,
        verify_tls: bool,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._health_timeout_seconds = health_timeout_seconds
        self._top_k = top_k
        self._candidate_k = candidate_k
        self._ready = False
        self._initialize_lock = asyncio.Lock()
        timeout = httpx.Timeout(
            timeout_seconds,
            connect=min(10.0, timeout_seconds),
        )
        self._client = httpx.AsyncClient(
            base_url=f"{base_url.rstrip('/')}/",
            timeout=timeout,
            verify=verify_tls,
            follow_redirects=False,
            limits=httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=max_connections,
            ),
            transport=transport,
        )

    async def initialize(self) -> None:
        async with self._initialize_lock:
            if self._ready:
                return
            try:
                response = await self._client.get(
                    "health/ready",
                    timeout=self._health_timeout_seconds,
                )
                payload = response.json()
                dependencies_ready = bool(
                    response.status_code == 200
                    and isinstance(payload, dict)
                    and payload.get("status") == "ok"
                )
                if not dependencies_ready:
                    self._ready = False
                    logger.warning(
                        "policy source is not ready",
                        extra={"status_code": response.status_code},
                    )
                    return
                auth_response = await self._client.get(
                    "v1/auth/check",
                    headers=self._request_headers(),
                    timeout=self._health_timeout_seconds,
                )
                auth_payload = auth_response.json()
                self._ready = bool(
                    auth_response.status_code == 200
                    and isinstance(auth_payload, dict)
                    and auth_payload.get("status") == "ok"
                )
                if not self._ready:
                    logger.warning(
                        "policy source authentication check failed",
                        extra={"status_code": auth_response.status_code},
                    )
            except (httpx.HTTPError, ValueError):
                self._ready = False
                logger.exception("policy source initialization failed")

    async def query(self, question: str) -> PolicyQueryResult:
        if not self._ready:
            await self.initialize()
        if not self._ready:
            raise PolicySourceUnavailableError(
                "政策知识服务尚未就绪"
            )

        try:
            response = await self._client.post(
                "v1/chat",
                headers=self._request_headers(),
                json={
                    "question": question,
                    "stream": False,
                    "top_k": self._top_k,
                    "candidate_k": self._candidate_k,
                },
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            self._ready = False
            raise PolicySourceUnavailableError(
                "政策知识服务连接失败"
            ) from exc

        if response.status_code in {401, 403, 503}:
            self._ready = False
            raise PolicySourceUnavailableError(
                "政策知识服务拒绝访问或尚未就绪"
            )
        if response.status_code >= 500:
            raise PolicySourceError("政策知识服务执行失败")
        if response.status_code != 200:
            raise PolicySourceError(
                f"政策知识服务返回非预期状态 {response.status_code}"
            )

        try:
            raw_payload = response.json()
            payload = RagChatPayload.model_validate(raw_payload)
        except (ValueError, ValidationError) as exc:
            raise PolicySourceContractError(
                "政策知识服务响应格式无效"
            ) from exc
        self._ready = True
        return PolicyQueryResult(
            answer=payload.answer,
            citations=tuple(
                PolicyCitation(**citation.model_dump())
                for citation in payload.citations
            ),
            finish_reason=payload.finish_reason,
            usage=dict(payload.usage),
        )

    def _request_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        request_id = current_request_id()
        if request_id != "-":
            headers["X-Request-ID"] = request_id
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def readiness(self) -> str:
        return "ready" if self._ready else "not_ready"

    async def close(self) -> None:
        self._ready = False
        await self._client.aclose()
