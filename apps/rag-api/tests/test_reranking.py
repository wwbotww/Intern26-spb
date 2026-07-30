from __future__ import annotations

import asyncio

import pytest
import torch

from spb_rag_api.adapters.reranker import (
    TransformerReranker,
    build_passage,
)
from spb_rag_api.domain.models import SearchHit, SearchQuery
from spb_rag_api.services.reranking import RerankingRetriever


def _hit(index: int) -> SearchHit:
    return SearchHit(
        chunk_id=f"chunk-{index}",
        document_id=f"document-{index}",
        title=f"政策标题 {index}",
        text=f"政策正文 {index}",
        source_url="https://www.spb.gov.cn/example.html",
        section_path=f"第{index}条",
        score=0.03 - index / 1000,
        document_no=f"文号-{index}",
        source_org="国家邮政局",
    )


class FakeRetriever:
    def __init__(self, hits: list[SearchHit]) -> None:
        self.hits = hits
        self.query: SearchQuery | None = None
        self.initialized = False

    async def initialize(self) -> None:
        self.initialized = True

    async def search(self, query: SearchQuery) -> list[SearchHit]:
        self.query = query
        return self.hits[: query.top_k]

    def readiness(self) -> dict[str, str]:
        return {"retriever": "ready"}

    async def close(self) -> None:
        self.initialized = False


class FakeReranker:
    def __init__(self, scores: list[float]) -> None:
        self.scores = scores
        self.initialized = False

    async def initialize(self) -> None:
        self.initialized = True

    async def score(
        self,
        *,
        query: str,
        hits: list[SearchHit],
    ) -> list[float]:
        assert query
        return self.scores[: len(hits)]

    def readiness(self) -> dict[str, str]:
        return {
            "reranker": "ready" if self.initialized else "not_ready"
        }

    async def close(self) -> None:
        self.initialized = False


def test_reranking_fetches_more_candidates_sorts_and_filters() -> None:
    async def scenario() -> tuple[
        list[SearchHit],
        FakeRetriever,
    ]:
        base = FakeRetriever([_hit(index) for index in range(5)])
        service = RerankingRetriever(
            retriever=base,
            reranker=FakeReranker([0.2, 0.91, 0.7, 0.4, 0.8]),
            fetch_k=20,
            min_score=0.5,
            shadow_mode=False,
        )
        await service.initialize()
        result = await service.search(
            SearchQuery(text="许可条件", top_k=3, candidate_k=40)
        )
        await service.close()
        return result, base

    result, base = asyncio.run(scenario())

    assert base.query is not None
    assert base.query.top_k == 20
    assert [hit.chunk_id for hit in result] == [
        "chunk-1",
        "chunk-4",
        "chunk-2",
    ]
    assert result[0].rerank_score == pytest.approx(0.91)


def test_reranking_gate_rejects_all_below_threshold() -> None:
    async def scenario(shadow_mode: bool) -> list[SearchHit]:
        service = RerankingRetriever(
            retriever=FakeRetriever([_hit(0), _hit(1)]),
            reranker=FakeReranker([0.2, 0.4]),
            fetch_k=10,
            min_score=0.5,
            shadow_mode=shadow_mode,
        )
        await service.initialize()
        return await service.search(
            SearchQuery(text="无关问题", top_k=2, candidate_k=10)
        )

    rejected = asyncio.run(scenario(False))
    assert rejected == []
    assert rejected.rejection_reason == "reranker_rejected"
    shadow = asyncio.run(scenario(True))
    assert [hit.chunk_id for hit in shadow] == ["chunk-1", "chunk-0"]


def test_transformer_reranker_batches_pairs_and_normalizes_logits() -> None:
    captured: dict[str, object] = {}

    class FakeTokenizer:
        def __call__(self, pairs: list[list[str]], **kwargs: object):
            captured["pairs"] = pairs
            captured.update(kwargs)
            return {
                "input_ids": torch.ones((len(pairs), 2), dtype=torch.long)
            }

    class FakeOutput:
        logits = torch.tensor([[-2.0], [2.0]])

    class FakeModel:
        def __call__(self, **kwargs: object) -> FakeOutput:
            assert kwargs["return_dict"] is True
            return FakeOutput()

    async def scenario() -> list[float]:
        reranker = TransformerReranker(
            model_name="BAAI/bge-reranker-base",
            device="cpu",
            batch_size=8,
            max_length=512,
            model_factory=lambda _name, _device: (
                FakeTokenizer(),
                FakeModel(),
            ),
        )
        return await reranker.score(
            query="问题",
            hits=[_hit(0), _hit(1)],
        )

    scores = asyncio.run(scenario())

    assert scores == pytest.approx([0.1192029, 0.8807970])
    assert captured["truncation"] == "only_second"
    assert captured["max_length"] == 512
    assert "标题：政策标题 0" in build_passage(_hit(0))
