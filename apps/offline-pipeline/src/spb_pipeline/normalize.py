from __future__ import annotations

import html
import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit


_WHITESPACE_RE = re.compile(r"[ \t\u00a0\u2002\u2003\u2009\u3000]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = html.unescape(value)
    # 法规标题、文号和正文中的全角标点具有展示与引用价值，不能用 NFKC
    # 将“〔〕”“（）”等机械改写为半角形式。
    value = unicodedata.normalize("NFC", value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_WHITESPACE_RE.sub(" ", line).strip() for line in value.splitlines()]
    return _BLANK_LINES_RE.sub("\n\n", "\n".join(lines)).strip()


def canonicalize_url(url: str) -> str:
    value = url.strip()
    parts = urlsplit(value)
    host = (parts.hostname or "").lower()
    scheme = parts.scheme.lower()
    if scheme == "http" and host in {"www.spb.gov.cn", "spb.gov.cn"}:
        scheme = "https"
    netloc = parts.netloc.lower()
    if parts.port in {80, 443}:
        netloc = host
    return urlunsplit((scheme, netloc, parts.path, parts.query, ""))


def validity_from_text(title: str, text: str = "") -> str:
    sample = f"{title}\n{text[:1000]}"
    if re.search(r"[（(]?\s*(失效|废止|已废止|已失效)\s*[）)]?", sample):
        return "invalid"
    if re.search(r"(现行有效|继续有效|有效期至)", sample):
        return "valid"
    return "unknown"


def safe_filename(value: str, fallback: str = "resource") -> str:
    value = clean_text(value)
    value = re.sub(r"[/\\:*?\"<>|\x00-\x1f]", "_", value)
    value = value.strip(" .")
    return (value or fallback)[:180]
