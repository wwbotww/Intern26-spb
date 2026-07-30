from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from ..domain.exceptions import (
    ChatProviderError,
    RelevanceJudgeError,
)
from ..domain.models import ChatEvent, RelevanceDecision, SearchHit


@dataclass(frozen=True)
class DeepSeekConfig:
    base_url: str
    api_key: str
    model: str = "deepseek-v4-flash"
    thinking: str = "disabled"
    timeout_seconds: float = 90.0
    max_tokens: int = 1200
    temperature: float = 0.1


@dataclass(frozen=True)
class DeepSeekJudgeConfig:
    base_url: str
    api_key: str
    model: str = "deepseek-v4-flash"
    timeout_seconds: float = 90.0
    max_sources: int = 5
    source_max_chars: int = 1200
    max_tokens: int = 180
    attempts: int = 2


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


JUDGE_SYSTEM_PROMPT = """你是政策知识库的证据充分性分类器，不回答用户问题。
请判断提供的资料是否能够直接、充分地支持回答用户问题。

判定规则：
1. 仅标题、主题或关键词相似不算可回答。
2. 条件、流程、材料、数字等问题必须有相应具体信息。
3. 涉及时间、效力或版本时，资料必须支持所问时间点。
4. 多条资料可以共同构成充分证据。
5. relevant_source_ids 只能使用输入资料中的整数 ID。
6. 只输出 JSON，不得输出解释性文字或答案。

JSON 示例：
{"answerable":true,"relevant_source_ids":[1,3],"reason_code":"direct_support"}
{"answerable":false,"relevant_source_ids":[],"reason_code":"topic_only"}"""


class DeepSeekRelevanceJudge:
    def __init__(
        self,
        config: DeepSeekJudgeConfig,
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

    async def assess(
        self,
        *,
        question: str,
        hits: list[SearchHit],
    ) -> RelevanceDecision:
        selected = hits[: self._config.max_sources]
        sources = [
            {
                "id": index,
                "title": hit.title,
                "document_no": hit.document_no,
                "section": hit.section_path,
                "text": hit.text[: self._config.source_max_chars],
            }
            for index, hit in enumerate(selected, start=1)
        ]
        payload = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "请根据下列输入输出 JSON 判定：\n"
                        + json.dumps(
                            {
                                "question": question,
                                "sources": sources,
                            },
                            ensure_ascii=False,
                        )
                    ),
                },
            ],
            "thinking": {"type": "disabled"},
            "temperature": 0,
            "max_tokens": self._config.max_tokens,
            "stream": False,
            "response_format": {"type": "json_object"},
        }

        last_error: Exception | None = None
        for _ in range(self._config.attempts):
            try:
                response = await self._client.post(
                    "chat/completions",
                    json=payload,
                )
                if response.is_error:
                    raise RelevanceJudgeError(
                        f"DeepSeek Judge HTTP {response.status_code}"
                    )
                envelope = response.json()
                content = (
                    ((envelope.get("choices") or [{}])[0].get("message")
                    or {}).get("content")
                    or ""
                ).strip()
                decision = self._parse_decision(
                    content=content,
                    source_count=len(selected),
                    usage=envelope.get("usage") or {},
                )
                return decision
            except RelevanceJudgeError as exc:
                last_error = exc
                if "HTTP" in str(exc):
                    break
            except (httpx.TimeoutException, httpx.HTTPError) as exc:
                raise RelevanceJudgeError(
                    "DeepSeek Judge 网络请求失败"
                ) from exc
            except (ValueError, TypeError, KeyError) as exc:
                last_error = exc
        raise RelevanceJudgeError(
            "DeepSeek Judge 未返回有效 JSON 判定"
        ) from last_error

    @staticmethod
    def _parse_decision(
        *,
        content: str,
        source_count: int,
        usage: dict[str, Any],
    ) -> RelevanceDecision:
        if not content:
            raise ValueError("Judge 返回空内容")
        data = json.loads(content)
        answerable = data.get("answerable")
        source_ids = data.get("relevant_source_ids")
        reason_code = data.get("reason_code")
        if not isinstance(answerable, bool):
            raise TypeError("answerable 必须为 bool")
        if not isinstance(source_ids, list) or not all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in source_ids
        ):
            raise TypeError("relevant_source_ids 必须为整数数组")
        unique_ids = tuple(dict.fromkeys(source_ids))
        if any(
            value < 1 or value > source_count
            for value in unique_ids
        ):
            raise ValueError("Judge 返回了不存在的来源 ID")
        if answerable and not unique_ids:
            raise ValueError("可回答判定必须包含来源 ID")
        if not isinstance(reason_code, str) or not reason_code.strip():
            raise TypeError("reason_code 必须为非空字符串")
        return RelevanceDecision(
            answerable=answerable,
            relevant_source_ids=unique_ids if answerable else (),
            reason_code=reason_code.strip(),
            usage=usage,
        )

    def readiness(self) -> dict[str, str]:
        return {
            "relevance_judge": (
                "ready"
                if self._config.api_key and not self._client.is_closed
                else "not_ready"
            )
        }

    async def close(self) -> None:
        if self._owns_client and not self._client.is_closed:
            await self._client.aclose()
