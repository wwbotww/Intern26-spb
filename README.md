# 国家邮政局政策知识库

该仓库采用 uv workspace，包含相互隔离的离线数据流水线、在线 RAG API
和共享数据契约。离线应用负责抓取、解析、OCR、向量化和 Milvus 写入；
在线应用只负责检索与问答，并只读访问 Milvus。

完整实施过程、技术选型、难点和项目总结见
[`docs/project-retrospective.md`](docs/project-retrospective.md)。
模块边界和依赖规则见
[`docs/workspace-architecture.md`](docs/workspace-architecture.md)。

## Workspace

```text
apps/offline-pipeline/   离线抓取、解析、OCR、向量化和入库
apps/rag-api/            在线检索与问答 API
packages/contracts/      两个应用共享的稳定数据契约
```

`rag-api` 禁止导入 `spb_pipeline`。两个应用不共享业务实现，只共享 collection
字段和 embedding 约束。

## 本地安装

```bash
UV_CACHE_DIR=/private/tmp/uv-cache \
  uv sync --python 3.11 --all-packages --all-extras --group dev
```

## 离线流水线

```bash
# 获取完整栏目清单
uv run --package spb-policy-pipeline spb-pipeline inventory

# 下载所有详情页
uv run --package spb-policy-pipeline spb-pipeline crawl-details

# 解析页面，并输出附件任务
uv run --package spb-policy-pipeline spb-pipeline parse

# 下载页面发现的附件
uv run --package spb-policy-pipeline spb-pipeline crawl-attachments

# 再次解析，将已下载附件并入结构化文档
uv run --package spb-policy-pipeline spb-pipeline parse

# macOS 上对扫描 PDF/图片执行 Vision OCR，再次解析
uv run --package spb-policy-pipeline spb-pipeline ocr
uv run --package spb-policy-pipeline spb-pipeline parse

# 切分和质量检查
uv run --package spb-policy-pipeline spb-pipeline chunk
uv run --package spb-policy-pipeline spb-pipeline report

# 使用 moka-ai/m3e-base 生成 768 维 dense vectors
uv run --package spb-policy-pipeline spb-pipeline \
  chunk --max-chars 360 --overlap-chars 50
uv run --package spb-policy-pipeline spb-pipeline embed

# Milvus：创建独立 collection、写入和只读检查
uv run --package spb-policy-pipeline spb-pipeline \
  milvus-create --uri http://host:19530 --database aisv
uv run --package spb-policy-pipeline spb-pipeline \
  milvus-ingest --uri http://host:19530 --database aisv
uv run --package spb-policy-pipeline spb-pipeline \
  milvus-sync --uri http://host:19530 --database aisv
uv run --package spb-policy-pipeline spb-pipeline \
  milvus-check --uri http://host:19530 --database aisv
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

离线配置示例位于
[`apps/offline-pipeline/.env.example`](apps/offline-pipeline/.env.example)。

## 在线 RAG API

阶段 2 已实现独立的只读检索服务：

- 使用 `moka-ai/m3e-base` 生成归一化的 768 维查询向量；
- 同时检索 HNSW/COSINE dense index 和 BM25 sparse index；
- 由 Milvus `RRFRanker` 融合两路候选；
- 支持文档类型、有效性、发布机构和发布日期结构化过滤；
- `/health/live` 只检查进程，`/health/ready` 检查模型和 Milvus；
- 在线进程不包含建表、写入、更新或删除接口。

```bash
# 将示例复制为部署环境变量，并填写只读 Milvus 连接信息
cp apps/rag-api/.env.example apps/rag-api/.env

# 当前配置使用 RAG_ 前缀
export RAG_MILVUS_URI=http://milvus-host:19530
export RAG_MILVUS_DATABASE=aisv
export RAG_MILVUS_COLLECTION=spb_policy_chunks

uv run --package spb-rag-api spb-rag-api
curl http://127.0.0.1:8080/health/live
curl http://127.0.0.1:8080/health/ready

curl -X POST http://127.0.0.1:8080/v1/retrieve \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "快递业务经营许可需要符合哪些条件？",
    "top_k": 5,
    "filters": {
      "validity_statuses": ["有效", "unknown"],
      "published_from": "2018-01-01"
    }
  }'
```

在线配置示例位于
[`apps/rag-api/.env.example`](apps/rag-api/.env.example)。
DeepSeek 问答和 SSE 流式输出将在阶段 3 接入，阶段 2 的接口只返回可验证的
检索结果。

## 测试

```bash
uv run pytest
```

## 数据边界

- 以栏目接口返回的 `channelCodeName=c100012` 为准，而不是按详情 URL 路径过滤。
- 栏目中的外链也保留在清单内；抓取失败会记录状态，不会从数据集中静默消失。
- “相关解读”仅记录关系，不自动作为政策正文，除非它本身也在栏目清单中。
- 未明确标注有效性的历史文件统一记为 `unknown`。
- 原始标准附件仅用于内部管理与检索，不应对外重新分发。
