from __future__ import annotations

from spb_pipeline.chunker import chunk_document


def test_legal_articles_are_hard_boundaries():
    document = {
        "document_id": "doc-1",
        "parent_document_id": None,
        "title": "测试法规",
        "source_url": "https://example.test/doc",
        "source_host": "example.test",
        "document_type": "html",
        "published_at": "2026-01-01",
        "document_no": "测试〔2026〕1号",
        "source_org": "测试机构",
        "validity_status": "unknown",
        "content_hash": "hash",
        "fetch_status": "success",
        "text": "第一条 内容。第二条 内容。",
        "blocks": [
            {"type": "heading", "text": "第一章 总则"},
            {"type": "article", "text": "第一条 第一条内容。"},
            {"type": "paragraph", "text": "第一条的第二款。"},
            {"type": "article", "text": "第二条 第二条内容。"},
        ],
    }

    chunks = chunk_document(document)

    assert len(chunks) == 2
    assert chunks[0]["section_path"] == "第一章 总则/第一条"
    assert "第一条的第二款" in chunks[0]["chunk_text"]
    assert chunks[1]["section_path"] == "第一章 总则/第二条"
    assert chunks[0]["chunk_id"] != chunks[1]["chunk_id"]


def test_table_is_split_by_row():
    document = {
        "document_id": "doc-table",
        "parent_document_id": None,
        "title": "标准目录",
        "source_url": "https://example.test/table",
        "source_host": "example.test",
        "document_type": "html",
        "published_at": "2026-01-01",
        "document_no": "",
        "source_org": "",
        "validity_status": "unknown",
        "content_hash": "hash",
        "fetch_status": "success",
        "text": "标准目录",
        "blocks": [
            {
                "type": "table",
                "headers": ["标准号", "标准名称"],
                "rows": [["YZ/T 1", "一"], ["YZ/T 2", "二"]],
            }
        ],
    }

    chunks = chunk_document(document)

    assert len(chunks) == 2
    assert "标准号 | 标准名称" in chunks[0]["chunk_text"]
    assert chunks[0]["section_path"] == "表格"
