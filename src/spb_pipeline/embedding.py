from __future__ import annotations

import os
import tempfile
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .config import Settings
from .io_utils import file_hash, read_jsonl, write_json_atomic


DEFAULT_MODEL = "moka-ai/m3e-base"


def _device_name(requested: str | None) -> str:
    if requested:
        return requested
    import torch

    return "mps" if torch.backends.mps.is_available() else "cpu"


def generate_embeddings(
    settings: Settings,
    *,
    model_name: str = DEFAULT_MODEL,
    batch_size: int = 64,
    device: str | None = None,
) -> dict[str, Any]:
    from sentence_transformers import SentenceTransformer

    chunks_path = settings.processed_dir / "chunks.jsonl"
    chunks = list(read_jsonl(chunks_path))
    if not chunks:
        raise RuntimeError("chunks.jsonl 为空，请先执行 chunk")
    selected_device = _device_name(device)
    model = SentenceTransformer(model_name, device=selected_device)
    max_tokens = max(
        len(
            model.tokenizer(
                chunk["embedding_input"],
                add_special_tokens=True,
                truncation=False,
            )["input_ids"]
        )
        for chunk in chunks
    )
    if max_tokens > model.max_seq_length:
        raise ValueError(
            f"存在 {max_tokens} tokens 的输入，超过模型上限 "
            f"{model.max_seq_length}；请减小 chunk"
        )
    chunk_ids = np.asarray([chunk["chunk_id"] for chunk in chunks])
    cached_vectors: dict[str, np.ndarray] = {}
    if (
        settings.embeddings_path.exists()
        and settings.embeddings_manifest_path.exists()
    ):
        previous_manifest = json.loads(
            settings.embeddings_manifest_path.read_text(encoding="utf-8")
        )
        if previous_manifest.get("model") == model_name:
            with np.load(
                settings.embeddings_path, allow_pickle=False
            ) as previous:
                cached_vectors = dict(
                    zip(
                        previous["chunk_ids"].tolist(),
                        np.asarray(previous["vectors"], dtype=np.float32),
                        strict=True,
                    )
                )
    missing_indexes = [
        index
        for index, chunk_id in enumerate(chunk_ids)
        if chunk_id not in cached_vectors
    ]
    dimension = int(model.get_embedding_dimension())
    vectors = np.empty((len(chunks), dimension), dtype=np.float32)
    for index, chunk_id in enumerate(chunk_ids):
        cached = cached_vectors.get(chunk_id)
        if cached is not None:
            vectors[index] = cached
    if missing_indexes:
        generated = model.encode(
            [chunks[index]["embedding_input"] for index in missing_indexes],
            batch_size=batch_size,
            show_progress_bar=True,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        vectors[missing_indexes] = np.asarray(generated, dtype=np.float32)

    settings.embeddings_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        dir=settings.embeddings_path.parent,
        prefix=f".{settings.embeddings_path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            np.savez_compressed(
                handle,
                chunk_ids=chunk_ids,
                vectors=vectors,
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, settings.embeddings_path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise

    manifest = {
        "model": model_name,
        "dimension": dimension,
        "normalized": True,
        "chunk_count": len(chunks),
        "max_tokens": max_tokens,
        "model_max_seq_length": model.max_seq_length,
        "device": selected_device,
        "reused_count": len(chunks) - len(missing_indexes),
        "generated_count": len(missing_indexes),
        "chunks_sha256": file_hash(chunks_path),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json_atomic(settings.embeddings_manifest_path, manifest)
    return manifest


def load_embedding_artifact(
    settings: Settings,
) -> tuple[list[dict[str, Any]], np.ndarray, dict[str, Any]]:
    chunks_path = settings.processed_dir / "chunks.jsonl"
    chunks = list(read_jsonl(chunks_path))
    manifest = json.loads(
        settings.embeddings_manifest_path.read_text(encoding="utf-8")
    )
    if manifest["chunks_sha256"] != file_hash(chunks_path):
        raise RuntimeError("chunks.jsonl 已变化，请重新生成 embeddings")
    with np.load(settings.embeddings_path, allow_pickle=False) as artifact:
        chunk_ids = artifact["chunk_ids"].tolist()
        vectors = np.asarray(artifact["vectors"], dtype=np.float32)
    expected_ids = [chunk["chunk_id"] for chunk in chunks]
    if chunk_ids != expected_ids:
        raise RuntimeError("embedding chunk_id 顺序与 chunks.jsonl 不一致")
    if vectors.shape != (len(chunks), int(manifest["dimension"])):
        raise RuntimeError(
            f"embedding shape 异常：{vectors.shape}，"
            f"预期 ({len(chunks)}, {manifest['dimension']})"
        )
    return chunks, vectors, manifest
