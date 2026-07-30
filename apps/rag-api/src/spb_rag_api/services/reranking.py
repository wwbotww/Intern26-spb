from __future__ import annotations

import logging
from dataclasses import replace
from time import perf_counter

from ..domain.exceptions import RerankerError
from ..domain.models import SearchHit, SearchQuery, SearchResults
from ..domain.ports import Reranker, Retriever
from ..observability.metrics import ServiceMetrics


logger = logging.getLogger(__name__)


class RerankingRetriever:
    def __init__(
        self,
        *,
        retriever: Retriever,
        reranker: Reranker,
        fetch_k: int,
        min_score: float,
        shadow_mode: bool,
        metrics: ServiceMetrics | None = None,
    ) -> None:
        self._retriever = retriever
        self._reranker = reranker
        self._fetch_k = fetch_k
        self._min_score = min_score
        self._shadow_mode = shadow_mode
        self._metrics = metrics
        self._ready = False

    async def initialize(self) -> None:
        await self._retriever.initialize()
        try:
            await self._reranker.initialize()
        except BaseException:
            await self._retriever.close()
            raise
        self._ready = True

    async def search(self, query: SearchQuery) -> list[SearchHit]:
        internal_top_k = max(
            query.top_k,
            min(self._fetch_k, query.candidate_k),
        )
        candidates = await self._retriever.search(
            replace(query, top_k=internal_top_k)
        )
        if not candidates:
            return SearchResults([], rejection_reason="no_context")

        started = perf_counter()
        scores = await self._reranker.score(
            query=query.text,
            hits=candidates,
        )
        duration_seconds = perf_counter() - started
        if len(scores) != len(candidates):
            raise RerankerError(
                "reranker 分数数量与候选数量不一致"
            )
        ranked = sorted(
            (
                replace(hit, rerank_score=float(score))
                for hit, score in zip(candidates, scores, strict=True)
            ),
            key=lambda hit: (
                hit.rerank_score
                if hit.rerank_score is not None
                else float("-inf")
            ),
            reverse=True,
        )
        accepted = (
            ranked
            if self._shadow_mode
            else [
                hit
                for hit in ranked
                if hit.rerank_score is not None
                and hit.rerank_score >= self._min_score
            ]
        )
        if not accepted:
            logger.info(
                "reranker_rejected_all",
                extra={
                    "candidate_count": len(ranked),
                    "top_rerank_score": round(
                        ranked[0].rerank_score or 0.0,
                        6,
                    ),
                    "threshold": self._min_score,
                },
            )
        if self._metrics is not None:
            self._metrics.observe_reranker(
                accepted=bool(accepted),
                top_score=float(ranked[0].rerank_score or 0.0),
                duration_seconds=duration_seconds,
            )
        if not accepted:
            return SearchResults(
                [],
                rejection_reason="reranker_rejected",
            )
        return SearchResults(accepted[: query.top_k])

    def readiness(self) -> dict[str, str]:
        checks = self._retriever.readiness()
        checks.update(self._reranker.readiness())
        checks["reranking"] = (
            "ready" if self._ready else "not_ready"
        )
        return checks

    async def close(self) -> None:
        self._ready = False
        await self._reranker.close()
        await self._retriever.close()
