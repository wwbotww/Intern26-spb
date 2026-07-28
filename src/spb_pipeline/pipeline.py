from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from .attachments import parse_attachment
from .config import Settings
from .crawler import detail_path_for
from .inventory import load_inventory
from .io_utils import read_jsonl, write_jsonl_atomic
from .parser import parse_page
from .state import CrawlState


def parse_documents(settings: Settings) -> dict[str, int]:
    settings.ensure_directories()
    inventory = load_inventory(settings.processed_dir / "inventory.jsonl")
    page_documents = []
    attachments: OrderedDict[str, dict] = OrderedDict()

    for item in inventory:
        html_path = detail_path_for(settings, item.document_id)
        page_document = parse_page(
            item, html_path if html_path.exists() else None
        )
        page_documents.append(page_document)
        for attachment in page_document.attachments:
            attachments.setdefault(attachment["attachment_id"], attachment)

    with CrawlState(settings.state_db) as state:
        attachment_documents = []
        parent_map = {document.document_id: document for document in page_documents}
        normalized_attachments: list[dict] = []
        for attachment in attachments.values():
            resource = state.get(attachment["attachment_id"])
            if resource:
                attachment["fetch_status"] = resource.status
                attachment["local_path"] = resource.local_path
            local_path = (
                Path(attachment["local_path"])
                if attachment.get("local_path")
                else None
            )
            parent = parent_map[attachment["parent_document_id"]]
            attachment_documents.append(
                parse_attachment(
                    attachment,
                    local_path=local_path,
                    parent_published_at=parent.published_at,
                    parent_document_no=parent.document_no,
                    parent_source_org=parent.source_org,
                    ocr_path=(
                        settings.ocr_dir
                        / f"{attachment['attachment_id']}.jsonl"
                    ),
                )
            )
            normalized_attachments.append(attachment)

    write_jsonl_atomic(
        settings.processed_dir / "attachments.jsonl",
        normalized_attachments,
    )
    all_documents = [*page_documents, *attachment_documents]
    write_jsonl_atomic(
        settings.processed_dir / "documents.jsonl",
        (document.to_dict() for document in all_documents),
    )
    return {
        "page_documents": len(page_documents),
        "attachments": len(normalized_attachments),
        "attachment_documents": len(attachment_documents),
        "documents": len(all_documents),
        "empty_documents": sum(not document.text for document in all_documents),
    }


def load_documents(settings: Settings) -> list[dict]:
    return list(read_jsonl(settings.processed_dir / "documents.jsonl"))
