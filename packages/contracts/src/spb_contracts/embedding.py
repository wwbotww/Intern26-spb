from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EmbeddingContract:
    model: str
    dimension: int
    normalized: bool
    metric: str
    max_sequence_tokens: int


M3E_BASE_CONTRACT = EmbeddingContract(
    model="moka-ai/m3e-base",
    dimension=768,
    normalized=True,
    metric="COSINE",
    max_sequence_tokens=512,
)
