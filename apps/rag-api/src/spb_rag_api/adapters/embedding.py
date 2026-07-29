from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np
from spb_contracts import EmbeddingContract, M3E_BASE_CONTRACT

from ..domain.exceptions import CollectionContractError, QueryTooLongError


ModelFactory = Callable[[str, str], Any]


class SentenceTransformerQueryEmbedder:
    def __init__(
        self,
        *,
        model_name: str,
        device: str,
        max_concurrency: int,
        contract: EmbeddingContract = M3E_BASE_CONTRACT,
        model_factory: ModelFactory | None = None,
    ) -> None:
        self._model_name = model_name
        self._device = device
        self._contract = contract
        self._model_factory = model_factory
        self._model: Any | None = None
        self._initialize_lock = asyncio.Lock()
        self._capacity = asyncio.Semaphore(max_concurrency)
        # Hugging Face fast tokenizers mutate shared truncation/padding state.
        # A shared SentenceTransformer instance therefore cannot safely encode
        # from multiple worker threads at once ("Already borrowed").
        self._encode_lock = asyncio.Lock()

    async def initialize(self) -> None:
        if self._model is not None:
            return
        async with self._initialize_lock:
            if self._model is None:
                self._model = await asyncio.to_thread(self._load_model)

    def _load_model(self) -> Any:
        if self._model_name != self._contract.model:
            raise CollectionContractError(
                f"查询模型必须为 {self._contract.model}，"
                f"当前为 {self._model_name}"
            )
        if self._model_factory is None:
            from sentence_transformers import SentenceTransformer

            model = SentenceTransformer(
                self._model_name,
                device=self._device,
            )
        else:
            model = self._model_factory(self._model_name, self._device)
        dimension = int(model.get_embedding_dimension())
        if dimension != self._contract.dimension:
            raise CollectionContractError(
                f"查询向量维度 {dimension} 与 collection 契约 "
                f"{self._contract.dimension} 不一致"
            )
        return model

    async def embed(self, text: str) -> Sequence[float]:
        await self.initialize()
        async with self._capacity:
            async with self._encode_lock:
                return await asyncio.to_thread(self._encode, text)

    def _encode(self, text: str) -> list[float]:
        model = self._model
        if model is None:
            raise RuntimeError("embedding 模型尚未初始化")
        token_ids = model.tokenizer(
            text,
            add_special_tokens=True,
            truncation=False,
        )["input_ids"]
        token_limit = min(
            self._contract.max_sequence_tokens,
            int(model.max_seq_length),
        )
        if len(token_ids) > token_limit:
            raise QueryTooLongError(
                f"查询包含 {len(token_ids)} tokens，超过上限 {token_limit}"
            )
        encoded = model.encode(
            [text],
            convert_to_numpy=True,
            normalize_embeddings=self._contract.normalized,
            show_progress_bar=False,
        )
        vector = np.asarray(encoded, dtype=np.float32)
        if vector.shape != (1, self._contract.dimension):
            raise CollectionContractError(
                f"查询向量 shape={vector.shape}，"
                f"预期 (1, {self._contract.dimension})"
            )
        return vector[0].tolist()

    async def close(self) -> None:
        self._model = None
