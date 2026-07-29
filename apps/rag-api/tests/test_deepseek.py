from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from spb_rag_api.adapters.deepseek import (
    DeepSeekChatProvider,
    DeepSeekConfig,
)
from spb_rag_api.domain.exceptions import ChatProviderError


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
