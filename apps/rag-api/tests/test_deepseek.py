from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from spb_rag_api.adapters.deepseek import (
    DeepSeekChatProvider,
    DeepSeekConfig,
    DeepSeekJudgeConfig,
    DeepSeekRelevanceJudge,
)
from spb_rag_api.domain.exceptions import (
    ChatProviderError,
    RelevanceJudgeError,
)
from spb_rag_api.domain.models import SearchHit


def _hit(index: int) -> SearchHit:
    return SearchHit(
        chunk_id=f"chunk-{index}",
        document_id=f"document-{index}",
        title=f"政策 {index}",
        text=f"政策正文 {index}",
        source_url="https://www.spb.gov.cn/example.html",
        section_path=f"第{index}条",
        score=0.03,
    )


def test_deepseek_provider_parses_stream_and_usage() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        stream = "\n".join(
            [
                ": keep-alive",
                'data: {"choices":[{"delta":{"content":"依据"},"finish_reason":null}],"usage":null}',
                'data: {"choices":[{"delta":{"content":"资料"},"finish_reason":"stop"}],"usage":null}',
                'data: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":2,"total_tokens":12}}',
                "data: [DONE]",
                "",
            ]
        )
        return httpx.Response(
            200,
            text=stream,
            headers={"content-type": "text/event-stream"},
        )

    async def scenario():
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://api.deepseek.com/",
        )
        provider = DeepSeekChatProvider(
            DeepSeekConfig(
                base_url="https://api.deepseek.com",
                api_key="test-key",
            ),
            client=client,
        )
        events = [
            event
            async for event in provider.stream(
                messages=[{"role": "user", "content": "问题"}]
            )
        ]
        await client.aclose()
        return events

    events = asyncio.run(scenario())

    assert captured["model"] == "deepseek-v4-flash"
    assert captured["thinking"] == {"type": "disabled"}
    assert captured["temperature"] == 0.1
    assert captured["stream_options"] == {"include_usage": True}
    assert [event.event for event in events] == [
        "keepalive",
        "delta",
        "delta",
        "usage",
        "done",
    ]
    assert events[-1].data["finish_reason"] == "stop"


def test_deepseek_provider_sanitizes_upstream_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"error": {"message": "secret upstream detail"}},
        )

    async def scenario() -> None:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://api.deepseek.com/",
        )
        provider = DeepSeekChatProvider(
            DeepSeekConfig(
                base_url="https://api.deepseek.com",
                api_key="test-key",
            ),
            client=client,
        )
        with pytest.raises(
            ChatProviderError,
            match="DeepSeek HTTP 429",
        ):
            async for _ in provider.stream(
                messages=[{"role": "user", "content": "问题"}]
            ):
                pass
        await client.aclose()

    asyncio.run(scenario())


def test_thinking_mode_omits_temperature() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            text="data: [DONE]\n\n",
            headers={"content-type": "text/event-stream"},
        )

    async def scenario() -> None:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://api.deepseek.com/",
        )
        provider = DeepSeekChatProvider(
            DeepSeekConfig(
                base_url="https://api.deepseek.com",
                api_key="test-key",
                thinking="enabled",
            ),
            client=client,
        )
        async for _ in provider.stream(
            messages=[{"role": "user", "content": "问题"}]
        ):
            pass
        await client.aclose()

    asyncio.run(scenario())

    assert captured["thinking"] == {"type": "enabled"}
    assert "temperature" not in captured


def test_relevance_judge_uses_json_mode_and_validates_sources() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "answerable": True,
                                    "relevant_source_ids": [2],
                                    "reason_code": "direct_support",
                                }
                            )
                        }
                    }
                ],
                "usage": {"total_tokens": 42},
            },
        )

    async def scenario():
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://api.deepseek.com/",
        )
        judge = DeepSeekRelevanceJudge(
            DeepSeekJudgeConfig(
                base_url="https://api.deepseek.com",
                api_key="test-key",
            ),
            client=client,
        )
        decision = await judge.assess(
            question="需要什么条件？",
            hits=[_hit(1), _hit(2)],
        )
        await client.aclose()
        return decision

    decision = asyncio.run(scenario())

    assert decision.answerable is True
    assert decision.relevant_source_ids == (2,)
    assert decision.usage["total_tokens"] == 42
    assert captured["stream"] is False
    assert captured["temperature"] == 0
    assert captured["response_format"] == {"type": "json_object"}
    assert "只输出 JSON" in captured["messages"][0]["content"]


def test_relevance_judge_retries_empty_json_and_rejects_invalid_ids() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        content = (
            ""
            if calls == 1
            else json.dumps(
                {
                    "answerable": True,
                    "relevant_source_ids": [9],
                    "reason_code": "direct_support",
                }
            )
        )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {},
            },
        )

    async def scenario() -> None:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://api.deepseek.com/",
        )
        judge = DeepSeekRelevanceJudge(
            DeepSeekJudgeConfig(
                base_url="https://api.deepseek.com",
                api_key="test-key",
                attempts=2,
            ),
            client=client,
        )
        with pytest.raises(
            RelevanceJudgeError,
            match="未返回有效 JSON",
        ):
            await judge.assess(
                question="问题",
                hits=[_hit(1)],
            )
        await client.aclose()

    asyncio.run(scenario())
    assert calls == 2
