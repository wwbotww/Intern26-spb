from __future__ import annotations

import pytest

from spb_pipeline.models import InventoryItem


@pytest.fixture
def inventory_item() -> InventoryItem:
    return InventoryItem(
        document_id="doc-001",
        title="测试管理规定",
        source_url="https://www.spb.gov.cn/gjyzj/c100009/c100012/test.shtml",
        published_at="2026-02-03 00:00:00",
        published_timestamp_ms=1769990400000,
        channel_code="c100012",
        channel_name="政策法规标准",
        api_content="接口兜底正文",
        api_resources=[],
        domain_metadata={"wh": "国邮测〔2026〕1号"},
        discovered_at="2026-07-27T00:00:00+00:00",
        inventory_hash="inventory-hash",
    )

