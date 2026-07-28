from __future__ import annotations

from spb_pipeline.inventory import normalize_inventory_record


def test_inventory_normalization_filters_string_null():
    record = {
        "manuscriptId": "abc",
        "title": " 测试&nbsp;标题 ",
        "url": "http://www.spb.gov.cn/test.shtml",
        "publishedTime": 1,
        "publishedTimeStr": "2026-01-01 00:00:00",
        "channelCodeName": "c100012",
        "channelName": "政策法规标准",
        "content": "正文",
        "resList": [],
        "domainMetaList": [
            {
                "resultList": [
                    {"key": "source", "value": "国家邮政局"},
                    {"key": "author", "value": "null"},
                ]
            }
        ],
    }

    item = normalize_inventory_record(record, "now")

    assert item.title == "测试 标题"
    assert item.source_url.startswith("https://")
    assert item.domain_metadata == {"source": "国家邮政局"}
