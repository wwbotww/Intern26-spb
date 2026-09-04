from __future__ import annotations

from time import perf_counter
from typing import Any

import httpx
from pydantic import ValidationError

from .schemas import (
    AgentEvalTurn,
    AgentPublicResponse,
    AgentTurnObservation,
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


class AgentApiClient:
    """Strict JSON client for the public Agent V2 black-box contract."""

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

    async def readiness(self) -> dict[str, Any]:
        response = await self._client.get("v2/agent/health/ready")
        self._raise_for_status(response, operation="readiness")
        payload = response.json()
        if not isinstance(payload, dict):
            raise EvalApiError("agent readiness 响应不是 JSON 对象")
        return payload

    async def capabilities(self) -> list[dict[str, Any]]:
        response = await self._client.get("v2/agent/capabilities")
        self._raise_for_status(response, operation="capabilities")
        payload = response.json()
        if not isinstance(payload, list) or not all(
            isinstance(item, dict) for item in payload
        ):
            raise EvalApiError("agent capabilities 响应不是对象数组")
        return payload

    async def send_message(
        self,
        turn: AgentEvalTurn,
        *,
        conversation_id: str | None,
        idempotency_key: str,
    ) -> AgentTurnObservation:
        started = perf_counter()
        payload: dict[str, Any] = {
            "confirm_overwrite": turn.confirm_overwrite,
            "stream": False,
        }
        if conversation_id is not None:
            payload["conversation_id"] = conversation_id
        if turn.message is not None:
            payload["message"] = turn.message
        if turn.explicit_intent is not None:
            payload["explicit_intent"] = turn.explicit_intent
        try:
            response = await self._client.post(
                "v2/agent/messages",
                headers={"Idempotency-Key": idempotency_key},
                json=payload,
            )
            elapsed_ms = round((perf_counter() - started) * 1000, 3)
            if response.is_error:
                return self._http_error_observation(response, elapsed_ms)
            raw = response.json()
            if not isinstance(raw, dict):
                raise EvalApiError("agent message 响应不是 JSON 对象")
            parsed = AgentPublicResponse.model_validate(raw)
            return AgentTurnObservation(
                status="ok",
                client_elapsed_ms=elapsed_ms,
                **parsed.model_dump(mode="json"),
            )
        except (
            EvalApiError,
            httpx.HTTPError,
            ValueError,
            TypeError,
            ValidationError,
        ) as exc:
            return AgentTurnObservation(
                status="error",
                client_elapsed_ms=round(
                    (perf_counter() - started) * 1000,
                    3,
                ),
                error=self._sanitize_error(exc),
            )

    @staticmethod
    def _raise_for_status(
        response: httpx.Response,
        *,
        operation: str,
    ) -> None:
        if response.is_error:
            raise EvalApiError(
                f"Agent API {operation} HTTP {response.status_code}"
            )

    @staticmethod
    def _http_error_observation(
        response: httpx.Response,
        elapsed_ms: float,
    ) -> AgentTurnObservation:
        code = ""
        category = ""
        retryable = False
        message = f"Agent API HTTP {response.status_code}"
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            detail = payload.get("detail", payload)
            if isinstance(detail, dict):
                code = str(detail.get("code", ""))[:200]
                category = str(detail.get("category", ""))[:200]
                retryable = detail.get("retryable") is True
                public_message = detail.get("message")
                if isinstance(public_message, str) and public_message:
                    message = public_message[:500]
        return AgentTurnObservation(
            status="error",
            client_elapsed_ms=elapsed_ms,
            http_status=response.status_code,
            error_code=code,
            error_category=category,
            retryable=retryable,
            error=message,
        )

    @staticmethod
    def _sanitize_error(exc: Exception) -> str:
        if isinstance(exc, httpx.TimeoutException):
            return "Agent API 请求超时"
        if isinstance(exc, httpx.HTTPError):
            return "Agent API 网络请求失败"
        if isinstance(exc, ValidationError):
            return "Agent API 响应未通过评测端契约校验"
        return str(exc)[:500]

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "AgentApiClient":
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> None:
        await self.close()
