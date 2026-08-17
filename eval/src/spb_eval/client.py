from __future__ import annotations

from time import perf_counter
from typing import Any

import httpx
from pydantic import ValidationError

from .schemas import (
    AssistantChatObservation,
    AssistantEvalCase,
    AssistantEvidenceItem,
    ChatObservation,
    CitationItem,
    EvalCase,
    RetrieveObservation,
    RetrievedItem,
)


class EvalApiError(RuntimeError):
    """A sanitized API or response-contract error."""


class RagApiClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {"Accept": "application/json"}
        if api_key:
            headers["X-API-Key"] = api_key
        self._client = httpx.AsyncClient(
            base_url=f"{base_url.rstrip('/')}/",
            headers=headers,
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
        )

    async def health(self) -> dict[str, Any]:
        response = await self._client.get("health/live")
        self._raise_for_status(response)
        payload = response.json()
        if not isinstance(payload, dict):
            raise EvalApiError("健康检查响应不是 JSON 对象")
        return payload

    async def retrieve(
        self,
        case: EvalCase,
        *,
        top_k: int,
        candidate_k: int,
    ) -> RetrieveObservation:
        started = perf_counter()
        try:
            response = await self._client.post(
                "v1/retrieve",
                json={
                    "query": case.question,
                    "top_k": top_k,
                    "candidate_k": candidate_k,
                    "filters": case.filters,
                },
            )
            elapsed_ms = (perf_counter() - started) * 1000
            self._raise_for_status(response)
            payload = response.json()
            if not isinstance(payload, dict):
                raise EvalApiError("retrieve 响应不是 JSON 对象")
            raw_results = payload.get("results", [])
            if not isinstance(raw_results, list):
                raise EvalApiError("retrieve results 不是数组")
            items = [
                RetrievedItem.model_validate(item)
                for item in raw_results
            ]
            return RetrieveObservation(
                status="ok",
                client_elapsed_ms=round(elapsed_ms, 3),
                server_elapsed_ms=payload.get("elapsed_ms"),
                mode=str(payload.get("mode", "")),
                results=items,
            )
        except (
            EvalApiError,
            httpx.HTTPError,
            ValueError,
            TypeError,
            ValidationError,
        ) as exc:
            return RetrieveObservation(
                status="error",
                client_elapsed_ms=round(
                    (perf_counter() - started) * 1000,
                    3,
                ),
                error=self._sanitize_error(exc),
            )

    async def chat(
        self,
        case: EvalCase,
        *,
        top_k: int,
        candidate_k: int,
    ) -> ChatObservation:
        started = perf_counter()
        try:
            response = await self._client.post(
                "v1/chat",
                json={
                    "question": case.question,
                    "stream": False,
                    "top_k": top_k,
                    "candidate_k": candidate_k,
                    "filters": case.filters,
                },
            )
            elapsed_ms = (perf_counter() - started) * 1000
            self._raise_for_status(response)
            payload = response.json()
            if not isinstance(payload, dict):
                raise EvalApiError("chat 响应不是 JSON 对象")
            raw_citations = payload.get("citations", [])
            if not isinstance(raw_citations, list):
                raise EvalApiError("chat citations 不是数组")
            citations = [
                CitationItem.model_validate(item)
                for item in raw_citations
            ]
            usage = payload.get("usage") or {}
            if not isinstance(usage, dict):
                raise EvalApiError("chat usage 不是 JSON 对象")
            return ChatObservation(
                status="ok",
                client_elapsed_ms=round(elapsed_ms, 3),
                finish_reason=str(payload.get("finish_reason", "")),
                answer=str(payload.get("answer", "")),
                citations=citations,
                usage=usage,
            )
        except (
            EvalApiError,
            httpx.HTTPError,
            ValueError,
            TypeError,
            ValidationError,
        ) as exc:
            return ChatObservation(
                status="error",
                client_elapsed_ms=round(
                    (perf_counter() - started) * 1000,
                    3,
                ),
                error=self._sanitize_error(exc),
            )

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.is_error:
            raise EvalApiError(f"RAG API HTTP {response.status_code}")

    @staticmethod
    def _sanitize_error(exc: Exception) -> str:
        if isinstance(exc, httpx.TimeoutException):
            return "RAG API 请求超时"
        if isinstance(exc, httpx.HTTPError):
            return "RAG API 网络请求失败"
        return str(exc)[:500]

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "RagApiClient":
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> None:
        await self.close()


class AssistantApiClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(
            base_url=f"{base_url.rstrip('/')}/",
            headers=headers,
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
        )

    async def health(self) -> dict[str, Any]:
        response = await self._client.get("health/live")
        self._raise_for_status(response)
        payload = response.json()
        if not isinstance(payload, dict):
            raise EvalApiError("assistant 健康检查响应不是 JSON 对象")
        return payload

    async def chat(
        self,
        case: AssistantEvalCase,
    ) -> AssistantChatObservation:
        started = perf_counter()
        try:
            response = await self._client.post(
                "v1/chat",
                json={
                    "mode": case.mode,
                    "question": case.question,
                    "stream": False,
                },
            )
            elapsed_ms = (perf_counter() - started) * 1000
            self._raise_for_status(response)
            payload = response.json()
            if not isinstance(payload, dict):
                raise EvalApiError("assistant chat 响应不是 JSON 对象")
            raw_evidence = payload.get("evidence", [])
            if not isinstance(raw_evidence, list):
                raise EvalApiError("assistant evidence 不是数组")
            evidence = [
                AssistantEvidenceItem.model_validate(item)
                for item in raw_evidence
            ]
            warnings = payload.get("warnings") or []
            missing_fields = payload.get("missing_fields") or []
            usage = payload.get("usage") or {}
            if not isinstance(warnings, list):
                raise EvalApiError("assistant warnings 不是数组")
            if not isinstance(missing_fields, list):
                raise EvalApiError("assistant missing_fields 不是数组")
            if not isinstance(usage, dict):
                raise EvalApiError("assistant usage 不是 JSON 对象")
            return AssistantChatObservation(
                status="ok",
                client_elapsed_ms=round(elapsed_ms, 3),
                request_id=str(payload.get("request_id", "")),
                mode=str(payload.get("mode", "")),
                answer=str(payload.get("answer", "")),
                evidence=evidence,
                warnings=[str(item) for item in warnings],
                missing_fields=[str(item) for item in missing_fields],
                used_tool=str(payload.get("used_tool", "")),
                finish_reason=str(payload.get("finish_reason", "")),
                reason_code=str(payload.get("reason_code", "")),
                usage=usage,
            )
        except (
            EvalApiError,
            httpx.HTTPError,
            ValueError,
            TypeError,
            ValidationError,
        ) as exc:
            return AssistantChatObservation(
                status="error",
                client_elapsed_ms=round(
                    (perf_counter() - started) * 1000,
                    3,
                ),
                error=self._sanitize_error(exc),
            )

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.is_error:
            raise EvalApiError(
                f"Assistant API HTTP {response.status_code}"
            )

    @staticmethod
    def _sanitize_error(exc: Exception) -> str:
        if isinstance(exc, httpx.TimeoutException):
            return "Assistant API 请求超时"
        if isinstance(exc, httpx.HTTPError):
            return "Assistant API 网络请求失败"
        return str(exc)[:500]

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "AssistantApiClient":
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> None:
        await self.close()
