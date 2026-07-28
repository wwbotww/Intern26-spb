from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class InventoryItem:
    document_id: str
    title: str
    source_url: str
    published_at: str
    published_timestamp_ms: int | None
    channel_code: str
    channel_name: str
    api_content: str
    api_resources: list[dict[str, Any]] = field(default_factory=list)
    domain_metadata: dict[str, str] = field(default_factory=dict)
    discovered_at: str = ""
    inventory_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AttachmentRef:
    attachment_id: str
    parent_document_id: str
    title: str
    source_url: str
    relation: str
    discovered_from: str
    media_type: str = ""
    local_path: str = ""
    fetch_status: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Document:
    document_id: str
    parent_document_id: str | None
    title: str
    source_url: str
    source_host: str
    document_type: str
    published_at: str
    document_no: str
    source_org: str
    validity_status: str
    template_type: str
    blocks: list[dict[str, Any]]
    text: str
    attachments: list[dict[str, Any]]
    related_links: list[dict[str, str]]
    content_hash: str
    fetch_status: str
    parse_method: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
