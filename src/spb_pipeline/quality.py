from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from .config import Settings
from .io_utils import read_jsonl, write_json_atomic
from .state import CrawlState


def _duplicates(records: list[dict], key: str) -> list[dict[str, Any]]:
    counts = Counter(record.get(key) for record in records if record.get(key))
    return [
        {key: value, "count": count}
        for value, count in counts.items()
        if count > 1
    ]


def build_quality_report(settings: Settings) -> dict[str, Any]:
    inventory = list(read_jsonl(settings.processed_dir / "inventory.jsonl"))
    attachments = list(
        read_jsonl(settings.processed_dir / "attachments.jsonl")
    )
    documents = list(read_jsonl(settings.processed_dir / "documents.jsonl"))
    chunks = list(read_jsonl(settings.processed_dir / "chunks.jsonl"))
    page_documents = [
        document
        for document in documents
        if document.get("parent_document_id") is None
    ]
    attachment_documents = [
        document
        for document in documents
        if document.get("parent_document_id") is not None
    ]
    inventory_ids = {record["document_id"] for record in inventory}
    document_ids = {record["document_id"] for record in page_documents}
    chunk_document_ids = {record["document_id"] for record in chunks}
    with CrawlState(settings.state_db) as state:
        crawl_counts = state.counts()

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": {
            "inventory": len(inventory),
            "page_documents": len(page_documents),
            "attachments": len(attachments),
            "attachment_documents": len(attachment_documents),
            "chunks": len(chunks),
            "non_spb_hosts": sum(
                "spb.gov.cn" not in record.get("source_url", "")
                for record in inventory
            ),
            "empty_api_content": sum(
                not record.get("api_content") for record in inventory
            ),
            "empty_documents": sum(
                not record.get("text") for record in documents
            ),
            "ocr_pending": sum(
                record.get("parse_method") == "ocr_pending"
                for record in attachment_documents
            ),
            "conversion_pending": sum(
                record.get("parse_method") == "conversion_pending"
                for record in attachment_documents
            ),
            "crawl_state": crawl_counts,
        },
        "coverage": {
            "inventory_without_page_document": sorted(inventory_ids - document_ids),
            "page_documents_without_chunks": sorted(
                document_ids - chunk_document_ids
            ),
        },
        "duplicates": {
            "inventory_document_id": _duplicates(inventory, "document_id"),
            "inventory_source_url": _duplicates(inventory, "source_url"),
            "chunk_id": _duplicates(chunks, "chunk_id"),
        },
        "anomalies": {
            "empty_page_documents": [
                {
                    "document_id": record["document_id"],
                    "title": record["title"],
                    "source_url": record["source_url"],
                    "fetch_status": record["fetch_status"],
                }
                for record in page_documents
                if not record.get("text")
            ],
            "failed_attachment_documents": [
                {
                    "document_id": record["document_id"],
                    "title": record["title"],
                    "source_url": record["source_url"],
                    "fetch_status": record["fetch_status"],
                    "parse_method": record["parse_method"],
                }
                for record in attachment_documents
                if record.get("fetch_status")
                in {"failed", "blocked", "parse_failed"}
            ],
        },
    }
    report["structural_checks_passed"] = (
        len(inventory) > 0
        and len(page_documents) == len(inventory)
        and not report["coverage"]["inventory_without_page_document"]
        and not report["duplicates"]["inventory_document_id"]
        and not report["duplicates"]["chunk_id"]
    )
    terminal_detail_count = sum(
        count
        for key, count in crawl_counts.items()
        if key.startswith("detail:")
        and key.split(":", 1)[1] in {"success", "failed", "blocked"}
    )
    terminal_attachment_count = sum(
        count
        for key, count in crawl_counts.items()
        if key.startswith("attachment:")
        and key.split(":", 1)[1] in {"success", "failed", "blocked"}
    )
    report["ready_for_milvus"] = (
        report["structural_checks_passed"]
        and terminal_detail_count == len(inventory)
        and terminal_attachment_count == len(attachments)
        and not report["coverage"]["page_documents_without_chunks"]
        and not any(
            not document.get("text") for document in attachment_documents
        )
    )
    write_json_atomic(settings.reports_dir / "quality-report.json", report)
    return report
