from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .io_utils import stable_id


CHAPTER_RE = re.compile(r"^(第[一二三四五六七八九十百零〇\d]+章[^\n]*)")
SECTION_RE = re.compile(r"^(第[一二三四五六七八九十百零〇\d]+节[^\n]*)")
ARTICLE_RE = re.compile(r"^(第[一二三四五六七八九十百零〇\d]+条)")


@dataclass(frozen=True)
class Segment:
    text: str
    section_path: str
    hard_boundary: bool = False


def _split_long_text(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            candidates = [
                text.rfind(mark, start + max_chars // 2, end)
                for mark in ("。", "；", "\n")
            ]
            boundary = max(candidates)
            if boundary > start:
                end = boundary + 1
        parts.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap_chars, start + 1)
    return [part for part in parts if part]


def _segments(document: dict[str, Any]) -> list[Segment]:
    chapter = ""
    section = ""
    article = ""
    segments: list[Segment] = []
    for block in document.get("blocks") or []:
        block_type = block.get("type")
        if block_type == "table":
            headers = [str(value) for value in block.get("headers") or []]
            prefix = " | ".join(headers)
            for row in block.get("rows") or []:
                text = " | ".join(str(value) for value in row)
                if prefix:
                    text = f"{prefix}\n{text}"
                segments.append(
                    Segment(
                        text=text,
                        section_path="/".join(
                            value for value in (chapter, section, "表格") if value
                        ),
                        hard_boundary=True,
                    )
                )
            continue
        text = str(block.get("text") or "").strip()
        if not text:
            continue
        chapter_match = CHAPTER_RE.match(text)
        section_match = SECTION_RE.match(text)
        article_match = (
            ARTICLE_RE.match(text) if block_type == "article" else None
        )
        if chapter_match:
            chapter = chapter_match.group(1)
            section = ""
            article = ""
            continue
        if section_match:
            section = section_match.group(1)
            article = ""
            continue
        if article_match:
            article = article_match.group(1)
        path = "/".join(value for value in (chapter, section, article) if value)
        segments.append(
            Segment(
                text=text,
                section_path=path,
                hard_boundary=bool(article_match) or block_type == "page",
            )
        )
    if not segments and document.get("text"):
        segments.append(Segment(text=document["text"], section_path=""))
    return segments


def chunk_document(
    document: dict[str, Any],
    *,
    max_chars: int = 1200,
    overlap_chars: int = 120,
) -> list[dict[str, Any]]:
    expanded: list[Segment] = []
    for segment in _segments(document):
        for part in _split_long_text(segment.text, max_chars, overlap_chars):
            expanded.append(
                Segment(
                    text=part,
                    section_path=segment.section_path,
                    hard_boundary=segment.hard_boundary,
                )
            )

    groups: list[tuple[str, str]] = []
    buffer: list[str] = []
    buffer_path = ""

    def flush() -> None:
        nonlocal buffer, buffer_path
        if buffer:
            groups.append(("\n\n".join(buffer), buffer_path))
            buffer = []
            buffer_path = ""

    for segment in expanded:
        if segment.hard_boundary:
            flush()
            buffer_path = segment.section_path
            buffer.append(segment.text)
            continue
        projected = len("\n\n".join([*buffer, segment.text]))
        if buffer and projected > max_chars:
            flush()
        if not buffer_path:
            buffer_path = segment.section_path
        buffer.append(segment.text)
    flush()

    chunks: list[dict[str, Any]] = []
    for index, (chunk_text, section_path) in enumerate(groups):
        context = [f"标题：{document['title']}"]
        if document.get("document_no"):
            context.append(f"文号：{document['document_no']}")
        if section_path:
            context.append(f"章节：{section_path}")
        context.append(f"正文：{chunk_text}")
        chunk_id = stable_id(
            document["document_id"],
            document["content_hash"],
            section_path,
            str(index),
        )
        chunks.append(
            {
                "chunk_id": chunk_id,
                "document_id": document["document_id"],
                "parent_document_id": document.get("parent_document_id"),
                "title": document["title"],
                "source_url": document["source_url"],
                "source_host": document.get("source_host", ""),
                "document_type": document.get("document_type", ""),
                "published_at": document.get("published_at", ""),
                "document_no": document.get("document_no", ""),
                "source_org": document.get("source_org", ""),
                "validity_status": document.get("validity_status", "unknown"),
                "section_path": section_path,
                "chunk_index": index,
                "chunk_text": chunk_text,
                "embedding_input": "\n".join(context),
                "content_hash": document["content_hash"],
                "fetch_status": document.get("fetch_status", ""),
            }
        )
    return chunks


def chunk_documents(
    documents: list[dict[str, Any]],
    *,
    max_chars: int = 1200,
    overlap_chars: int = 120,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for document in documents:
        if not document.get("text"):
            continue
        result.extend(
            chunk_document(
                document,
                max_chars=max_chars,
                overlap_chars=overlap_chars,
            )
        )
    return result
