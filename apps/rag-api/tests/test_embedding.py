from __future__ import annotations

import asyncio
import threading
import time

import numpy as np
import pytest

from spb_rag_api.adapters.embedding import (
    SentenceTransformerQueryEmbedder,
)
from spb_rag_api.domain.exceptions import QueryTooLongError


class FakeModel:
    max_seq_length = 512

    def get_embedding_dimension(self) -> int:
        return 768

    def tokenizer(
        self,
        text: str,
        *,
        add_special_tokens: bool,
        truncation: bool,
    ) -> dict[str, list[int]]:
        assert add_special_tokens is True
        assert truncation is False
        return {"input_ids": list(range(len(text)))}

    def encode(self, texts: list[str], **kwargs: object) -> np.ndarray:
        assert kwargs["normalize_embeddings"] is True
        assert texts
        return np.ones((1, 768), dtype=np.float32)


def _factory(model_name: str, device: str) -> FakeModel:
    assert model_name == "moka-ai/m3e-base"
    assert device == "cpu"
    return FakeModel()


def test_embedder_uses_contract_and_normalization() -> None:
    async def scenario() -> list[float]:
        embedder = SentenceTransformerQueryEmbedder(
            model_name="moka-ai/m3e-base",
            device="cpu",
            max_concurrency=2,
            model_factory=_factory,
        )
        await embedder.initialize()
        result = await embedder.embed("邮政业标准")
        await embedder.close()
        return list(result)

    vector = asyncio.run(scenario())

    assert len(vector) == 768
    assert vector[0] == 1.0


def test_embedder_rejects_query_over_model_token_limit() -> None:
    async def scenario() -> None:
        embedder = SentenceTransformerQueryEmbedder(
            model_name="moka-ai/m3e-base",
            device="cpu",
            max_concurrency=1,
            model_factory=_factory,
        )
        await embedder.initialize()
        with pytest.raises(QueryTooLongError):
            await embedder.embed("邮" * 513)

    asyncio.run(scenario())


def test_embedder_serializes_access_to_shared_model() -> None:
    class ConcurrencyTrackingModel(FakeModel):
        def __init__(self) -> None:
            self.active = 0
            self.peak = 0
            self.lock = threading.Lock()

        def encode(
            self,
            texts: list[str],
            **kwargs: object,
        ) -> np.ndarray:
            with self.lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            time.sleep(0.02)
            with self.lock:
                self.active -= 1
            return super().encode(texts, **kwargs)

    model = ConcurrencyTrackingModel()

    async def scenario() -> None:
        embedder = SentenceTransformerQueryEmbedder(
            model_name="moka-ai/m3e-base",
            device="cpu",
            max_concurrency=5,
            model_factory=lambda _name, _device: model,
        )
        await asyncio.gather(
            *(embedder.embed(f"查询 {index}") for index in range(5))
        )

    asyncio.run(scenario())

    assert model.peak == 1
