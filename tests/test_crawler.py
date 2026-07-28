from __future__ import annotations

from spb_pipeline.crawler import DUPLICATE_EXTENSION_RE


def test_duplicate_attachment_extension_repair():
    url = "https://example.test/files/政策正文.pdf.pdf"

    assert DUPLICATE_EXTENSION_RE.sub(r"\1", url).endswith("政策正文.pdf")


def test_single_attachment_extension_is_unchanged():
    url = "https://example.test/files/政策正文.pdf"

    assert DUPLICATE_EXTENSION_RE.sub(r"\1", url) == url
