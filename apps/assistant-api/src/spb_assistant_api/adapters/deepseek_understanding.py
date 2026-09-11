from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Mapping
from contextlib import suppress
from time import perf_counter
from typing import Any, NoReturn

import httpx

from ..domain.agent_errors import AgentOperationError
from ..domain.failures import AgentFailure, FailureCategory
from ..domain.understanding import StructuredModelUnderstanding
from ..domain.intents import Intent
from .agent_http import AgentJsonHttpClient

logger = logging.getLogger("spb_assistant_api.query_model")


class DeepSeekQueryUnderstandingModel:
    """One bounded JSON classification attempt; no tools or provider retries.

    Credentials and the HTTP client belong to this adapter, never Graph State.
    The optional Hybrid fallback decides what to do when classification fails.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 8,
        max_tokens: int = 768,
        max_concurrency: int = 2,
        max_response_bytes: int = 65536,
        transport: httpx.AsyncBaseTransport | None = None,
        call_observer: Callable[[Mapping[str, Any]], None] | None = None,
        product_price_enabled: bool = False,
    ) -> None:
        if not api_key.strip() or not model.strip():
            raise ValueError("模型名称和 API Key 不能为空")
        if not 128 <= max_tokens <= 4096:
            raise ValueError("max_tokens 必须在 128 到 4096 之间")
        self._model = model.strip()
        self._timeout = timeout_seconds
        self._max_tokens = max_tokens
        self._call_observer = call_observer
        self._product_price_enabled = product_price_enabled
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._http = AgentJsonHttpClient(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            max_connections=max_concurrency,
            verify_tls=True,
            max_response_bytes=max_response_bytes,
            default_headers={"Authorization": f"Bearer {api_key.strip()}"},
            transport=transport,
        )

    async def classify(
        self,
        *,
        message: str,
        prompt: str,
        prompt_version: str,
    ) -> Mapping[str, Any]:
        started = perf_counter()
        outcome = "failure"
        failure: AgentFailure | None = None
        usage: dict[str, int] = {}
        try:
            # Include connection-pool and semaphore wait in the total deadline.
            async with asyncio.timeout(self._timeout), self._semaphore:
                response = await self._http.request_json(
                    capability="query_understanding",
                    method="POST",
                    path="chat/completions",
                    json_body={
                        "model": self._model,
                        "messages": [
                            {
                                "role": "system",
                                "content": self._system_prompt(
                                    prompt, prompt_version, product_price_enabled=self._product_price_enabled,
                                ),
                            },
                            {"role": "user", "content": message},
                        ],
                        "response_format": {"type": "json_object"},
                        "thinking": {"type": "disabled"},
                        "stream": False,
                        "temperature": 0,
                        "max_tokens": self._max_tokens,
                    },
                )
                usage = _safe_usage(response.payload)
                parsed = self._parse_completion(response.payload, product_price_enabled=self._product_price_enabled)
                outcome = "success"
                return parsed
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except TimeoutError:
            failure = AgentFailure(
                category=FailureCategory.UPSTREAM_TIMEOUT,
                code="query_model_deadline_exceeded",
                message="意图理解模型超过本次时间预算",
            )
            raise AgentOperationError(failure) from None
        except AgentOperationError as exc:
            # Preserve stable categories, never propagate raw provider bodies.
            failure = exc.failure
            raise AgentOperationError(failure) from None
        finally:
            measurement = {
                "provider": "deepseek",
                "outcome": outcome,
                "duration_ms": round((perf_counter() - started) * 1000, 3),
                "failure_category": failure.category.value
                if failure
                else None,
                "failure_code": failure.code if failure else None,
                **usage,
            }
            if self._call_observer is not None:
                with suppress(Exception):
                    self._call_observer(measurement.copy())
            # Optional telemetry must not alter business behavior, even if a
            # custom logging handler fails. No prompt/content/URL/Key is logged.
            with suppress(Exception):
                logger.info(
                    "query_model_call",
                    extra=measurement,
                )

    @staticmethod
    def _system_prompt(prompt: str, prompt_version: str, *, product_price_enabled: bool = False) -> str:
        document = StructuredModelUnderstanding.model_json_schema()
        excluded = "device_price" if product_price_enabled else "product_price"
        document["$defs"]["Intent"]["enum"] = [item for item in document["$defs"]["Intent"]["enum"] if item != excluded]
        schema = json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return f"{prompt}\nPrompt version: {prompt_version}\nJSON Schema:\n{schema}"

    @staticmethod
    def _parse_completion(payload: Any, *, product_price_enabled: bool = False) -> dict[str, Any]:
        if not isinstance(payload, dict):
            _contract_error("query_model_envelope_invalid")
        choices = payload.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            _contract_error("query_model_envelope_invalid")
        choice = choices[0]
        if not isinstance(choice, dict):
            _contract_error("query_model_envelope_invalid")
        if choice.get("finish_reason") != "stop":
            _contract_error("query_model_incomplete_output")
        message = choice.get("message")
        if (
            not isinstance(message, dict)
            or message.get("role") != "assistant"
            or message.get("tool_calls")
            or message.get("function_call")
        ):
            _contract_error("query_model_envelope_invalid")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            _contract_error("query_model_empty_output")
        try:
            raw = json.loads(
                content,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
            parsed = StructuredModelUnderstanding.model_validate(raw)
            excluded = Intent.DEVICE_PRICE if product_price_enabled else Intent.PRODUCT_PRICE
            if any(candidate.intent is excluded for candidate in parsed.candidates):
                raise ValueError("price understanding profile mismatch")
        except (ValueError, TypeError, RecursionError):
            _contract_error("query_model_schema_invalid")
        # reasoning_content, provider IDs and any other envelope fields die here.
        return parsed.model_dump(mode="json")

    async def close(self) -> None:
        await self._http.close()


def _contract_error(code: str) -> NoReturn:
    raise AgentOperationError(
        AgentFailure(
            category=FailureCategory.CONTRACT_VIOLATION,
            code=code,
            message="意图理解模型响应未通过输出契约校验",
        )
    ) from None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> NoReturn:
    raise ValueError("Non-finite JSON number")


def _safe_usage(payload: Any) -> dict[str, int]:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return {}
    return {
        name: value
        for name in ("prompt_tokens", "completion_tokens", "total_tokens")
        if type(value := usage.get(name)) is int and 0 <= value <= 1_000_000
    }
