from __future__ import annotations

from dataclasses import replace

from spb_pipeline.parser import parse_page


COMMON_HTML = """<!doctype html>
<html>
<head>
  <meta name="ArticleTitle" content="邮政业测试管理规定">
</head>
<body>
  <div class="content">
    <h2>邮政业测试管理规定</h2>
    <div class="info"><p class="fl">日期：2026-02-03</p></div>
    <div class="detail-news">
      <p style="text-align:center"><strong>第一章 总则</strong></p>
      <p>第一条 为了测试解析器，制定本规定。</p>
      <p>第二条 本规定适用于测试。</p>
      <p>附件：<a href="files/template.docx">审核模板</a></p>
      <p>相关解读：<a href="/interpretation.shtml">规定解读</a></p>
    </div>
  </div>
</body>
</html>
"""


DISCLOSURE_HTML = """<!doctype html>
<html>
<head><meta name="ArticleTitle" content="附件型政策"></head>
<body>
  <div class="content-head">
    <ul><li>索 引 号：</li><li>000/2026-1</li><li>发布机构：</li><li>国家邮政局</li></ul>
    <ul><li>发文字号：</li><li>国邮发〔2026〕2号</li></ul>
  </div>
  <div class="content-body">
    <h3>附件型政策</h3>
    <div class="fujian"><p>附件：</p><a href="files/policy.pdf">政策正文.pdf</a></div>
  </div>
</body>
</html>
"""


TABLE_HTML = """<!doctype html>
<html><body>
<div class="content"><h2>标准目录</h2><div class="detail-news">
<table>
  <tr><th>标准号</th><th>标准名称</th></tr>
  <tr><td>YZ/T 0001</td><td><a href="files/one.pdf">测试标准一</a></td></tr>
  <tr><td>YZ/T 0002</td><td>测试标准二</td></tr>
</table>
</div></div>
</body></html>
"""

GOV_CN_HTML = """<!doctype html>
<html><body>
  <div class="trs_editor_view">
    <p>第一条 为促进快递业健康发展，保障快递安全。</p>
  </div>
</body></html>
"""


WECHAT_HTML = """<!doctype html>
<html><body>
  <div id="js_content">
    <p>政策解读正文。</p>
  </div>
</body></html>
"""

GOV_CN_LEGACY_HTML = """<!doctype html>
<html><body>
  <div id="UCAP-CONTENT" class="pages_content">
    <p>国务院办公厅关于降低物流成本的实施意见。</p>
  </div>
</body></html>
"""


def test_parse_common_detail(tmp_path, inventory_item):
    path = tmp_path / "common.html"
    path.write_text(COMMON_HTML, encoding="utf-8")

    document = parse_page(inventory_item, path)

    assert document.template_type == "common_detail"
    assert document.title == "邮政业测试管理规定"
    assert document.document_no == "国邮测〔2026〕1号"
    assert "第一条" in document.text
    assert len(document.attachments) == 1
    assert document.attachments[0]["source_url"].endswith("/files/template.docx")
    assert len(document.related_links) == 1


def test_parse_disclosure_detail(tmp_path, inventory_item):
    path = tmp_path / "disclosure.html"
    path.write_text(DISCLOSURE_HTML, encoding="utf-8")
    item = replace(
        inventory_item,
        document_id="doc-002",
        title="附件型政策",
        api_content="",
        domain_metadata={},
    )

    document = parse_page(item, path)

    assert document.template_type == "disclosure_detail"
    assert document.document_no == "国邮发〔2026〕2号"
    assert len(document.attachments) == 1
    assert document.fetch_status == "empty"


def test_parse_table_preserves_rows(tmp_path, inventory_item):
    path = tmp_path / "table.html"
    path.write_text(TABLE_HTML, encoding="utf-8")

    document = parse_page(inventory_item, path)

    table = next(block for block in document.blocks if block["type"] == "table")
    assert table["headers"] == ["标准号", "标准名称"]
    assert table["rows"][0] == ["YZ/T 0001", "测试标准一"]
    assert len(document.attachments) == 1


def test_api_fallback(inventory_item):
    document = parse_page(inventory_item, None)

    assert document.template_type == "api_fallback"
    assert document.text == "接口兜底正文"
    assert document.fetch_status == "fallback"


def test_parse_gov_cn_detail(tmp_path, inventory_item):
    path = tmp_path / "gov-cn.html"
    path.write_text(GOV_CN_HTML, encoding="utf-8")
    item = replace(
        inventory_item,
        document_id="gov-doc",
        title="快递暂行条例",
        source_url="https://www.gov.cn/zhengce/content/example.htm",
    )

    document = parse_page(item, path)

    assert document.template_type == "gov_cn_detail"
    assert "促进快递业健康发展" in document.text


def test_parse_wechat_article(tmp_path, inventory_item):
    path = tmp_path / "wechat.html"
    path.write_text(WECHAT_HTML, encoding="utf-8")
    item = replace(
        inventory_item,
        document_id="wechat-doc",
        title="政策解读",
        source_url="https://mp.weixin.qq.com/s/example",
    )

    document = parse_page(item, path)

    assert document.template_type == "wechat_article"
    assert "政策解读正文" in document.text


def test_parse_gov_cn_legacy_content(tmp_path, inventory_item):
    path = tmp_path / "gov-cn-legacy.html"
    path.write_text(GOV_CN_LEGACY_HTML, encoding="utf-8")
    item = replace(
        inventory_item,
        document_id="gov-legacy-doc",
        title="物流政策",
        source_url="https://www.gov.cn/zhengce/content/legacy.htm",
    )

    document = parse_page(item, path)

    assert document.template_type == "gov_cn_content"
    assert "降低物流成本" in document.text
