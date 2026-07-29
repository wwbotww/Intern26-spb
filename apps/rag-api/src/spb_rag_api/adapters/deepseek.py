from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from ..domain.exceptions import ChatProviderError
from ..domain.models import ChatEvent


@dataclass(frozen=True)
class DeepSeekConfig:
    base_url: str
    api_key: str
    model: str = "deepseek-v4-flash"
    thinking: str = "disabled"
    timeout_seconds: float = 90.0
    max_tokens: int = 1200
    temperature: float = 0.1


class DeepSeekChatProvider:
    def __init__(
        self,
        config: DeepSeekConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=f"{config.base_url.rstrip('/')}/",
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(
                connect=min(10.0, config.timeout_seconds),
                read=config.timeout_seconds,
                write=min(30.0, config.timeout_seconds),
                pool=min(10.0, config.timeout_seconds),
            ),
        )

    @property
    def model(self) -> str:
        return self._config.model

    def readiness(self) -> dict[str, str]:
        return {
            "deepseek": (
                "ready"
                if self._config.api_key and not self._client.is_closed
                else "not_ready"
            )
        }

    async def stream(
        self,
        *,
        messages: list[dict[str, str]],
    ) -> AsyncIterator[ChatEvent]:
        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": messages,
            "thinking": {"type": self._config.thinking},
            "max_tokens": self._config.max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if self._config.thinking == "disabled":
            payload["temperature"] = self._config.temperature

        finish_reason: str | None = None
        try:
            async with self._client.stream(
                "POST",
                "chat/completions",
                json=payload,
            ) as response:
                if response.is_error:
                    await response.aread()
                    raise ChatProviderError(
                        f"DeepSeek HTTP {response.status_code}"
                    )
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    if line.startswith(":"):
                        yield ChatEvent(event="keepalive", data={})
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        yield ChatEvent(
                            event="done",
                            data={"finish_reason": finish_reason or "stop"},
                        )
                        return
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError as exc:
                        raise ChatProviderError(
                            "DeepSeek 返回了无效的 SSE JSON"
                        ) from exc
                    usage = chunk.get("usage")
                    if usage:
                        yield ChatEvent(event="usage", data=usage)
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    finish_reason = (
                        choice.get("finish_reason") or finish_reason
                    )
                    content = (choice.get("delta") or {}).get("content")
                    if content:
                        yield ChatEvent(
                            event="delta",
                            data={"content": content},
                        )
        except ChatProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise ChatProviderError("DeepSeek 请求超时") from exc
        except httpx.HTTPError as exc:
            raise ChatProviderError("DeepSeek 网络请求失败") from exc
        raise ChatProviderError("DeepSeek 流在 [DONE] 前意外结束")

    async def close(self) -> None:
        if self._owns_client and not self._client.is_closed:
            await self._client.aclose()
