# 国家邮政局政策法规标准数据流水线

该项目抓取国家邮政局“政策法规标准”栏目的完整清单，归档详情页与附件，
解析为结构化文档，使用 `moka-ai/m3e-base` 生成向量，并同步到 Milvus
独立 collection。

完整实施过程、技术选型、难点和项目总结见
[`docs/project-retrospective.md`](docs/project-retrospective.md)。

## 本地安装

```bash
UV_CACHE_DIR=/private/tmp/uv-cache uv sync --python 3.11 --extra dev
```

## 常用命令

```bash
# 获取完整栏目清单
uv run spb-pipeline inventory

# 下载所有详情页
uv run spb-pipeline crawl-details

# 解析页面，并输出附件任务
uv run spb-pipeline parse

# 下载页面发现的附件
uv run spb-pipeline crawl-attachments

# 再次解析，将已下载附件并入结构化文档
uv run spb-pipeline parse

# macOS 上对扫描 PDF/图片执行 Vision OCR，再次解析
uv run spb-pipeline ocr
uv run spb-pipeline parse

# 切分和质量检查
uv run spb-pipeline chunk
uv run spb-pipeline report

# 使用 moka-ai/m3e-base 生成 768 维 dense vectors
uv run spb-pipeline chunk --max-chars 360 --overlap-chars 50
uv run spb-pipeline embed

# Milvus：创建独立 collection、写入和只读检查
uv run spb-pipeline milvus-create --uri http://host:19530 --database aisv
uv run spb-pipeline milvus-ingest --uri http://host:19530 --database aisv
uv run spb-pipeline milvus-sync --uri http://host:19530 --database aisv
uv run spb-pipeline milvus-check --uri http://host:19530 --database aisv

# 按上述顺序执行完整前置流程
uv run spb-pipeline run
```

所有步骤均可重复执行。`data/state/crawl.db` 保存抓取状态，已成功下载且文件
仍存在的资源默认不会重复请求。

## 主要产物

```text
data/raw/inventory/       原始栏目接口响应
data/raw/html/            原始详情页
data/raw/attachments/     原始附件
data/state/crawl.db       断点和错误状态
data/processed/inventory.jsonl
data/processed/attachments.jsonl
data/processed/documents.jsonl
data/processed/chunks.jsonl
data/reports/quality-report.json
```

`chunks.jsonl` 同时提供 `chunk_text` 与 `embedding_input`。当前 dense embedding
使用 `moka-ai/m3e-base`（768 维、归一化），Milvus collection 同时提供
HNSW/COSINE dense 检索和内置 BM25 sparse 检索。

扫描件 OCR 使用 macOS Vision，并需要系统中存在 `pdftoppm`；找不到时可通过
`PDFTOPPM_BIN` 指定 Poppler 可执行文件。OCR 结果按附件 ID 保存于
`data/processed/ocr/`，可断点续跑。

## 数据边界

- 以栏目接口返回的 `channelCodeName=c100012` 为准，而不是按详情 URL 路径过滤。
- 栏目中的外链也保留在清单内；抓取失败会记录状态，不会从数据集中静默消失。
- “相关解读”仅记录关系，不自动作为政策正文，除非它本身也在栏目清单中。
- 未明确标注有效性的历史文件统一记为 `unknown`。
- 原始标准附件仅用于内部管理与检索，不应对外重新分发。
