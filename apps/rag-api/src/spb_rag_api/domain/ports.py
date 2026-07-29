from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Protocol

from .models import ChatEvent, SearchHit, SearchQuery


class QueryEmbedder(Protocol):
    async def initialize(self) -> None: ...

    async def embed(self, text: str) -> Sequence[float]: ...

    async def close(self) -> None: ...


class HybridSearchStore(Protocol):
    async def initialize(self) -> None: ...

    async def hybrid_search(
        self,
        query: SearchQuery,
        dense_vector: Sequence[float],
    ) -> list[SearchHit]: ...

    async def close(self) -> None: ...


class Retriever(Protocol):
    async def initialize(self) -> None: ...

    async def search(self, query: SearchQuery) -> list[SearchHit]: ...

    def readiness(self) -> dict[str, str]: ...

    async def close(self) -> None: ...


class ChatProvider(Protocol):
    @property
    def model(self) -> str: ...

    async def stream(
        self,
        *,
        messages: list[dict[str, str]],
    ) -> AsyncIterator[ChatEvent]: ...

    def readiness(self) -> dict[str, str]: ...

    async def close(self) -> None: ...
