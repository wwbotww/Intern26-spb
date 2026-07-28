from __future__ import annotations

from ..domain.exceptions import RetrievalNotReadyError
from ..domain.models import SearchHit, SearchQuery
from ..domain.ports import HybridSearchStore, QueryEmbedder


class HybridSearchService:
    def __init__(
        self,
        *,
        embedder: QueryEmbedder,
        store: HybridSearchStore,
    ) -> None:
        self._embedder = embedder
        self._store = store
        self._ready = False

    async def initialize(self) -> None:
        await self._embedder.initialize()
        try:
            await self._store.initialize()
        except BaseException:
            await self._embedder.close()
            raise
        self._ready = True

    async def search(self, query: SearchQuery) -> list[SearchHit]:
        if not self._ready:
            raise RetrievalNotReadyError("检索服务尚未初始化")
        vector = await self._embedder.embed(query.text)
        return await self._store.hybrid_search(query, vector)

    def readiness(self) -> dict[str, str]:
        return {
            "retriever": "ready" if self._ready else "not_ready",
            "embedding": "ready" if self._ready else "not_ready",
            "milvus": "ready" if self._ready else "not_ready",
        }

    async def close(self) -> None:
        self._ready = False
        await self._store.close()
        await self._embedder.close()
