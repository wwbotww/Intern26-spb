from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .config import Settings
from .http_client import DownloadError, HttpClient
from .inventory import load_inventory
from .io_utils import read_jsonl, stable_id
from .normalize import canonicalize_url, safe_filename
from .state import CrawlState


DUPLICATE_EXTENSION_RE = re.compile(
    r"(\.(?:pdf|doc|docx))\1(?=$|[?#])", re.IGNORECASE
)


def _detail_path(settings: Settings, document_id: str) -> Path:
    return settings.raw_html_dir / f"{safe_filename(document_id)}.html"


def _attachment_path(
    settings: Settings, parent_document_id: str, attachment_id: str, title: str, url: str
) -> Path:
    suffix = Path(unquote(urlsplit(url).path)).suffix.lower()
    if not suffix:
        suffix = mimetypes.guess_extension(
            mimetypes.guess_type(title)[0] or ""
        ) or ".bin"
    stem = safe_filename(Path(title).stem, attachment_id[:16])
    return (
        settings.raw_attachment_dir
        / safe_filename(parent_document_id)
        / f"{stem}-{attachment_id[:12]}{suffix}"
    )


def _fetch_resource(
    *,
    client: HttpClient,
    state: CrawlState,
    resource_id: str,
    document_id: str,
    parent_document_id: str | None,
    kind: str,
    source_url: str,
    destination: Path,
    force: bool,
) -> str:
    canonical_url = canonicalize_url(source_url)
    state.discover(
        resource_id=resource_id,
        document_id=document_id,
        parent_document_id=parent_document_id,
        kind=kind,
        source_url=source_url,
        canonical_url=canonical_url,
        local_path=str(destination),
    )
    current = state.get(resource_id)
    if (
        not force
        and current
        and current.status == "success"
        and destination.exists()
    ):
        return "skipped"
    candidates = [canonical_url]
    repaired_url = DUPLICATE_EXTENSION_RE.sub(r"\1", canonical_url)
    if repaired_url != canonical_url:
        candidates.append(repaired_url)
    errors: list[str] = []
    last_error: DownloadError | None = None
    for candidate_url in candidates:
        try:
            result = client.download(candidate_url, destination)
            state.mark_success(
                resource_id,
                http_status=int(result["http_status"]),
                content_type=str(result["content_type"]),
                content_length=int(result["content_length"]),
                etag=str(result["etag"]),
                last_modified=str(result["last_modified"]),
                sha256=str(result["sha256"]),
            )
            return "success"
        except DownloadError as exc:
            last_error = exc
            errors.append(str(exc))
    assert last_error is not None
    blocked = (
        urlsplit(canonical_url).hostname == "mp.weixin.qq.com"
        and last_error.status_code in {401, 403, 418}
    )
    state.mark_failure(
        resource_id,
        error_message=" | ".join(errors),
        http_status=last_error.status_code,
        status="blocked" if blocked else "failed",
    )
    return "blocked" if blocked else "failed"


def crawl_details(
    settings: Settings,
    *,
    force: bool = False,
    limit: int | None = None,
    document_ids: set[str] | None = None,
) -> dict[str, int]:
    settings.ensure_directories()
    inventory = load_inventory(settings.processed_dir / "inventory.jsonl")
    if document_ids:
        inventory = [
            item for item in inventory if item.document_id in document_ids
        ]
    if limit is not None:
        inventory = inventory[:limit]
    counts = {"success": 0, "skipped": 0, "failed": 0, "blocked": 0}
    with CrawlState(settings.state_db) as state, HttpClient(settings) as client:
        for item in inventory:
            outcome = _fetch_resource(
                client=client,
                state=state,
                resource_id=stable_id("detail", item.source_url),
                document_id=item.document_id,
                parent_document_id=None,
                kind="detail",
                source_url=item.source_url,
                destination=_detail_path(settings, item.document_id),
                force=force,
            )
            counts[outcome] += 1
    return counts


def crawl_attachments(
    settings: Settings,
    *,
    force: bool = False,
    limit: int | None = None,
    attachment_ids: set[str] | None = None,
) -> dict[str, int]:
    settings.ensure_directories()
    attachment_path = settings.processed_dir / "attachments.jsonl"
    counts = {"success": 0, "skipped": 0, "failed": 0, "blocked": 0}
    with CrawlState(settings.state_db) as state, HttpClient(settings) as client:
        attachment_records = list(read_jsonl(attachment_path))
        if attachment_ids:
            attachment_records = [
                attachment
                for attachment in attachment_records
                if attachment["attachment_id"] in attachment_ids
            ]
        if limit is not None:
            attachment_records = attachment_records[:limit]
        for attachment in attachment_records:
            destination = _attachment_path(
                settings,
                attachment["parent_document_id"],
                attachment["attachment_id"],
                attachment["title"],
                attachment["source_url"],
            )
            outcome = _fetch_resource(
                client=client,
                state=state,
                resource_id=attachment["attachment_id"],
                document_id=attachment["attachment_id"],
                parent_document_id=attachment["parent_document_id"],
                kind="attachment",
                source_url=attachment["source_url"],
                destination=destination,
                force=force,
            )
            counts[outcome] += 1
    return counts


def detail_path_for(settings: Settings, document_id: str) -> Path:
    return _detail_path(settings, document_id)
