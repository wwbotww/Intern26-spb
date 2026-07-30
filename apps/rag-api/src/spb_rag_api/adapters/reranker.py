from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from ..domain.exceptions import RerankerError
from ..domain.models import SearchHit


ModelFactory = Callable[[str, str], tuple[Any, Any]]


def build_passage(hit: SearchHit) -> str:
    fields = [
        f"标题：{hit.title}",
        f"文号：{hit.document_no}" if hit.document_no else "",
        f"章节：{hit.section_path}" if hit.section_path else "",
        f"发布机构：{hit.source_org}" if hit.source_org else "",
        f"正文：{hit.text}",
    ]
    return "\n".join(field for field in fields if field)


class TransformerReranker:
    def __init__(
        self,
        *,
        model_name: str,
        device: str,
        batch_size: int,
        max_length: int,
        max_concurrency: int = 1,
        model_factory: ModelFactory | None = None,
    ) -> None:
        self._model_name = model_name
        self._device = device
        self._batch_size = batch_size
        self._max_length = max_length
        self._model_factory = model_factory
        self._tokenizer: Any | None = None
        self._model: Any | None = None
        self._initialize_lock = asyncio.Lock()
        self._capacity = asyncio.Semaphore(max_concurrency)

    async def initialize(self) -> None:
        if self._model is not None:
            return
        async with self._initialize_lock:
            if self._model is None:
                tokenizer, model = await asyncio.to_thread(
                    self._load_model
                )
                self._tokenizer = tokenizer
                self._model = model

    def _load_model(self) -> tuple[Any, Any]:
        if self._model_factory is not None:
            return self._model_factory(self._model_name, self._device)
        from transformers import (
            AutoModelForSequenceClassification,
            AutoTokenizer,
        )

        tokenizer = AutoTokenizer.from_pretrained(self._model_name)
        model = AutoModelForSequenceClassification.from_pretrained(
            self._model_name
        )
        model.to(self._device)
        model.eval()
        return tokenizer, model

    async def score(
        self,
        *,
        query: str,
        hits: list[SearchHit],
    ) -> list[float]:
        if not hits:
            return []
        await self.initialize()
        async with self._capacity:
            return await asyncio.to_thread(self._score_sync, query, hits)

    def _score_sync(
        self,
        query: str,
        hits: list[SearchHit],
    ) -> list[float]:
        tokenizer = self._tokenizer
        model = self._model
        if tokenizer is None or model is None:
            raise RerankerError("reranker 尚未初始化")

        import torch

        scores: list[float] = []
        passages = [build_passage(hit) for hit in hits]
        try:
            for start in range(0, len(passages), self._batch_size):
                batch = passages[start : start + self._batch_size]
                pairs = [[query, passage] for passage in batch]
                inputs = tokenizer(
                    pairs,
                    padding=True,
                    truncation="only_second",
                    max_length=self._max_length,
                    return_tensors="pt",
                )
                inputs = {
                    name: value.to(self._device)
                    for name, value in inputs.items()
                }
                with torch.inference_mode():
                    logits = model(
                        **inputs,
                        return_dict=True,
                    ).logits.view(-1).float()
                    normalized = torch.sigmoid(logits)
                scores.extend(
                    float(value)
                    for value in normalized.detach().cpu().tolist()
                )
        except RerankerError:
            raise
        except Exception as exc:
            raise RerankerError("reranker 推理失败") from exc
        return scores

    def readiness(self) -> dict[str, str]:
        return {
            "reranker": (
                "ready" if self._model is not None else "not_ready"
            )
        }

    async def close(self) -> None:
        self._model = None
        self._tokenizer = None
