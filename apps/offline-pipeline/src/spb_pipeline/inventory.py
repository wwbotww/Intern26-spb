from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Settings
from .http_client import HttpClient
from .io_utils import json_hash, write_jsonl_atomic
from .models import InventoryItem
from .normalize import canonicalize_url, clean_text


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _metadata(record: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for metadata_set in record.get("domainMetaList") or []:
        for item in metadata_set.get("resultList") or []:
            key = str(item.get("key") or "").strip()
            value = clean_text(str(item.get("value") or ""))
            if key and value and value.lower() != "null":
                result[key] = value
    return result


def normalize_inventory_record(
    record: dict[str, Any], discovered_at: str
) -> InventoryItem:
    normalized_for_hash = {
        "manuscriptId": record.get("manuscriptId"),
        "title": record.get("title"),
        "url": record.get("url"),
        "publishedTime": record.get("publishedTime"),
        "content": record.get("content"),
        "resList": record.get("resList") or [],
        "domainMetaList": record.get("domainMetaList") or [],
    }
    return InventoryItem(
        document_id=str(record.get("manuscriptId") or "").strip(),
        title=clean_text(record.get("title") or record.get("subTitle") or ""),
        source_url=canonicalize_url(str(record.get("url") or "")),
        published_at=str(record.get("publishedTimeStr") or ""),
        published_timestamp_ms=record.get("publishedTime"),
        channel_code=str(record.get("channelCodeName") or ""),
        channel_name=clean_text(record.get("channelName") or ""),
        api_content=clean_text(record.get("content") or ""),
        api_resources=list(record.get("resList") or []),
        domain_metadata=_metadata(record),
        discovered_at=discovered_at,
        inventory_hash=json_hash(normalized_for_hash),
    )


def fetch_inventory(settings: Settings) -> tuple[list[InventoryItem], int]:
    settings.ensure_directories()
    discovered_at = _now_utc()
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    items: dict[str, InventoryItem] = {}
    expected_total: int | None = None

    with HttpClient(settings) as client:
        page = 1
        while True:
            params: dict[str, str | int] = {
                "_isAgg": "true",
                "_isJson": "true",
                "_pageSize": settings.page_size,
                "_template": "index",
                "_rangeTimeGte": "",
                "_channelName": "",
                "page": page,
            }
            payload, _ = client.get_json(settings.inventory_endpoint, params)
            raw_path = settings.raw_inventory_dir / (
                f"{timestamp}-page-{page:03d}.json"
            )
            raw_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            data = payload.get("data") or {}
            if expected_total is None:
                expected_total = int(data.get("total") or 0)
            results = list(data.get("results") or [])
            for record in results:
                item = normalize_inventory_record(record, discovered_at)
                if not item.document_id:
                    raise ValueError(f"第 {page} 页存在缺少 manuscriptId 的记录")
                if item.channel_code != settings.channel_code:
                    raise ValueError(
                        f"{item.document_id} 栏目代码异常: {item.channel_code}"
                    )
                items[item.document_id] = item
            if not results or len(items) >= expected_total:
                break
            page += 1
            if page > math.ceil(expected_total / settings.page_size) + 1:
                raise RuntimeError("栏目分页超过预期边界")

    ordered = sorted(
        items.values(),
        key=lambda item: (
            item.published_timestamp_ms or 0,
            item.document_id,
        ),
        reverse=True,
    )
    if expected_total is None or len(ordered) != expected_total:
        raise RuntimeError(
            f"栏目清单不完整：接口声明 {expected_total}，实际 {len(ordered)}"
        )
    output = settings.processed_dir / "inventory.jsonl"
    write_jsonl_atomic(output, (item.to_dict() for item in ordered))
    return ordered, expected_total


def load_inventory(path: Path) -> list[InventoryItem]:
    from .io_utils import read_jsonl

    return [InventoryItem(**record) for record in read_jsonl(path)]
