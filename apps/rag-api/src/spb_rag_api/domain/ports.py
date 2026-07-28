from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Protocol

from .models import ChatEvent, SearchHit, SearchQuery


class QueryEmbedder(Protocol):
    async def embed(self, text: str) -> Sequence[float]: ...


class Retriever(Protocol):
    async def search(self, query: SearchQuery) -> list[SearchHit]: ...


class ChatProvider(Protocol):
    async def stream(
        self,
        *,
        messages: list[dict[str, str]],
    ) -> AsyncIterator[ChatEvent]: ...
