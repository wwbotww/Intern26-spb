from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from .io_utils import json_hash, stable_id
from .models import AttachmentRef, Document, InventoryItem
from .normalize import clean_text, validity_from_text


FILE_EXTENSION_RE = re.compile(
    r"\.(pdf|docx?|xlsx?|xls|csv|txt|rtf|zip|rar|jpg|jpeg|png|tif|tiff)"
    r"(?:$|[?#])",
    re.IGNORECASE,
)
CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十百零〇\d]+章")
SECTION_RE = re.compile(r"^第[一二三四五六七八九十百零〇\d]+节")
ARTICLE_RE = re.compile(r"^第[一二三四五六七八九十百零〇\d]+条")


def _read_html(path: Path) -> str:
    payload = path.read_bytes()
    for encoding in ("utf-8", "gb18030"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


def _meta(soup: BeautifulSoup, name: str) -> str:
    tag = soup.find("meta", attrs={"name": name})
    return clean_text(tag.get("content", "")) if isinstance(tag, Tag) else ""


def _template_and_container(
    soup: BeautifulSoup,
) -> tuple[str, Tag | None]:
    container = soup.select_one(".detail-news")
    if isinstance(container, Tag):
        if container.select_one(".TRS_Editor"):
            return "trs_detail", container
        return "common_detail", container
    container = soup.select_one(".content-body")
    if isinstance(container, Tag):
        return "disclosure_detail", container
    container = soup.select_one(".trs_editor_view")
    if isinstance(container, Tag):
        return "gov_cn_detail", container
    container = soup.select_one("#UCAP-CONTENT")
    if isinstance(container, Tag):
        return "gov_cn_content", container
    container = soup.select_one("#js_content")
    if isinstance(container, Tag):
        return "wechat_article", container
    container = soup.find("article")
    if isinstance(container, Tag):
        return "generic_article", container
    container = soup.find("main")
    if isinstance(container, Tag):
        return "generic_main", container
    return "api_fallback", None


def _looks_like_heading(tag: Tag, text: str) -> bool:
    if tag.name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        return True
    if CHAPTER_RE.match(text) or SECTION_RE.match(text):
        return True
    style = (tag.get("style") or "").lower()
    align = (tag.get("align") or "").lower()
    centered = "text-align: center" in style or align == "center"
    return centered and bool(tag.find(["strong", "b"]))


def _table_block(table: Tag) -> dict[str, Any] | None:
    rows: list[list[str]] = []
    for row in table.find_all("tr"):
        cells = [clean_text(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]
        if any(cells):
            rows.append(cells)
    if not rows:
        return None
    has_header = bool(table.find("th"))
    headers = rows[0] if has_header else []
    data_rows = rows[1:] if has_header else rows
    return {"type": "table", "headers": headers, "rows": data_rows}


def extract_blocks(container: Tag) -> list[dict[str, Any]]:
    working_soup = BeautifulSoup(str(container), "lxml")
    working = working_soup.body.find() if working_soup.body else working_soup.find()
    if not isinstance(working, Tag):
        return []
    for unwanted in working.select(
        "script, style, noscript, .fujian, .ewm, #QRCode, .share, .printIco"
    ):
        unwanted.decompose()

    candidates = working.find_all(
        ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table", "div"]
    )
    blocks: list[dict[str, Any]] = []
    for tag in candidates:
        if tag.name == "table":
            block = _table_block(tag)
            if block:
                blocks.append(block)
            continue
        if tag.find_parent("table"):
            continue
        if tag.name == "div" and tag.find(
            ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table", "div"],
            recursive=False,
        ):
            continue
        text = clean_text(tag.get_text("\n", strip=True))
        if not text:
            continue
        if blocks and blocks[-1].get("text") == text:
            continue
        if _looks_like_heading(tag, text):
            block_type = "heading"
        elif ARTICLE_RE.match(text):
            block_type = "article"
        elif tag.name == "li":
            block_type = "list_item"
        else:
            block_type = "paragraph"
        blocks.append({"type": block_type, "text": text})

    if not blocks:
        fallback = clean_text(working.get_text("\n", strip=True))
        if fallback:
            blocks.append({"type": "paragraph", "text": fallback})
    return blocks


def blocks_to_text(blocks: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for block in blocks:
        if block["type"] == "table":
            headers = block.get("headers") or []
            if headers:
                parts.append(" | ".join(headers))
            parts.extend(" | ".join(row) for row in block.get("rows") or [])
        else:
            parts.append(block.get("text") or "")
    return clean_text("\n\n".join(part for part in parts if part))


def _visible_metadata(soup: BeautifulSoup) -> dict[str, str]:
    metadata: dict[str, str] = {}
    head = soup.select_one(".content-head")
    if isinstance(head, Tag):
        text = clean_text(head.get_text("\n", strip=True))
        patterns = {
            "index_number": r"索\s*引\s*号[:：]?\s*([^\n]+)",
            "source_org": r"发布机构[:：]?\s*([^\n]+)",
            "document_no": r"发文字号[:：]?\s*([^\n]+)",
            "category": r"分\s*类[:：]?\s*([^\n]+)",
        }
        for key, pattern in patterns.items():
            match = re.search(pattern, text)
            if match:
                metadata[key] = clean_text(match.group(1))
    return metadata


def _link_title(anchor: Tag) -> str:
    return clean_text(
        anchor.get_text(" ", strip=True)
        or anchor.get("title")
        or Path(urlsplit(anchor.get("href") or "").path).name
    )


def discover_links(
    soup: BeautifulSoup,
    container: Tag | None,
    item: InventoryItem,
) -> tuple[list[AttachmentRef], list[dict[str, str]]]:
    attachments: dict[str, AttachmentRef] = {}
    related_links: dict[str, dict[str, str]] = {}
    scopes: list[Tag] = []
    if container:
        scopes.append(container)
    for selector in (".fujian", "#fujian"):
        tag = soup.select_one(selector)
        if isinstance(tag, Tag) and tag not in scopes:
            scopes.append(tag)

    for scope in scopes:
        for anchor in scope.find_all("a", href=True):
            href = str(anchor.get("href") or "").strip()
            if not href or href.startswith(("javascript:", "#")):
                continue
            absolute_url = urljoin(item.source_url, href)
            title = _link_title(anchor)
            context = clean_text(anchor.parent.get_text(" ", strip=True)) if anchor.parent else ""
            if FILE_EXTENSION_RE.search(absolute_url):
                attachment_id = stable_id(item.document_id, absolute_url)
                relation = "attachment" if "附件" in context else "inline_file"
                attachments[absolute_url] = AttachmentRef(
                    attachment_id=attachment_id,
                    parent_document_id=item.document_id,
                    title=title,
                    source_url=absolute_url,
                    relation=relation,
                    discovered_from="html",
                )
            elif "解读" in context or "相关" in context:
                related_links[absolute_url] = {
                    "title": title,
                    "url": absolute_url,
                    "relation": "related_interpretation",
                }

    for resource in item.api_resources:
        if resource.get("ispublished") is not True:
            continue
        candidate = str(
            resource.get("filePathNew") or resource.get("filePath") or ""
        ).strip()
        if not candidate:
            continue
        absolute_url = urljoin(item.source_url, candidate)
        if not FILE_EXTENSION_RE.search(absolute_url):
            continue
        if absolute_url in attachments:
            continue
        title = clean_text(
            resource.get("fileName")
            or resource.get("title")
            or Path(urlsplit(absolute_url).path).name
        )
        attachments[absolute_url] = AttachmentRef(
            attachment_id=stable_id(item.document_id, absolute_url),
            parent_document_id=item.document_id,
            title=title,
            source_url=absolute_url,
            relation="attachment",
            discovered_from="api_resList",
        )
    return list(attachments.values()), list(related_links.values())


def parse_page(item: InventoryItem, html_path: Path | None) -> Document:
    soup: BeautifulSoup | None = None
    template_type = "api_fallback"
    container: Tag | None = None
    metadata = dict(item.domain_metadata)
    title = item.title
    blocks: list[dict[str, Any]] = []
    attachments: list[AttachmentRef] = []
    related_links: list[dict[str, str]] = []
    fetch_status = "fallback"

    if html_path and html_path.exists():
        soup = BeautifulSoup(_read_html(html_path), "lxml")
        template_type, container = _template_and_container(soup)
        title = (
            _meta(soup, "ArticleTitle")
            or clean_text(
                (
                    soup.select_one(".content h2")
                    or soup.select_one(".content-body h3")
                    or soup.find("h1")
                ).get_text(" ", strip=True)
            )
            if (
                soup.select_one(".content h2")
                or soup.select_one(".content-body h3")
                or soup.find("h1")
            )
            else item.title
        )
        visible_metadata = _visible_metadata(soup)
        for key, value in visible_metadata.items():
            if value:
                metadata[key] = value
        if container:
            blocks = extract_blocks(container)
            if (
                blocks
                and blocks[0].get("type") == "heading"
                and clean_text(blocks[0].get("text")) == clean_text(title)
            ):
                blocks = blocks[1:]
        attachments, related_links = discover_links(soup, container, item)
        fetch_status = "success"

    if not blocks and item.api_content:
        blocks = [{"type": "paragraph", "text": clean_text(item.api_content)}]
        template_type = "api_fallback"
    text = blocks_to_text(blocks)
    source_org = clean_text(
        metadata.get("source_org") or metadata.get("source") or ""
    )
    document_no = clean_text(metadata.get("document_no") or metadata.get("wh") or "")
    content_hash = json_hash(
        {
            "title": title,
            "blocks": blocks,
            "attachments": [attachment.source_url for attachment in attachments],
        }
    )
    return Document(
        document_id=item.document_id,
        parent_document_id=None,
        title=title,
        source_url=item.source_url,
        source_host=(urlsplit(item.source_url).hostname or "").lower(),
        document_type="html",
        published_at=item.published_at,
        document_no=document_no,
        source_org=source_org,
        validity_status=validity_from_text(title, text),
        template_type=template_type,
        blocks=blocks,
        text=text,
        attachments=[attachment.to_dict() for attachment in attachments],
        related_links=related_links,
        content_hash=content_hash,
        fetch_status=fetch_status if text else "empty",
        parse_method=template_type,
        metadata=metadata,
    )
