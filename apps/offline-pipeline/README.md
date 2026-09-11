# Offline Pipeline

[项目全貌](../../README.md) · [全局模块边界](../../docs/workspace-architecture.md) · [数据源职责](../../docs/data-sources.md)

本模块是政策知识的数据生产者：抓取 → 解析/OCR → 切分 → embedding → Milvus 写入。
它不运行 HTTP 问答、不管理 Agent 会话，也不负责设备价格数据采集。
RAG 只读消费它的产物；两者共享 [contracts](../../packages/contracts/README.md)，不相互导入实现。

## 运行与产物

以下命令都从仓库根目录执行，先确认目标、网络、数据目录和写入权限。

仅做 Agent 开发不需要执行流水线。先完成[工作区安装](../../docs/development.md)；运行 embedding/Milvus 时再安装 extras：

~~~bash
uv sync --frozen --all-packages --all-extras --group dev --python 3.12
~~~

处理扫描 PDF 还需 Poppler `pdftoppm` 和当前 macOS Vision OCR；
首次模型加载需要下载权重。配置样例在
[.env.example](.env.example)，实际文件不入 Git。
CLI 从进程环境读取；配置好私有文件后在专用终端导入：

~~~bash
set -a
source apps/offline-pipeline/.env
set +a
~~~

按需执行，而非接手时自动重抓/重建已有数据：

~~~bash
.venv/bin/spb-pipeline inventory
.venv/bin/spb-pipeline crawl-details
.venv/bin/spb-pipeline parse
.venv/bin/spb-pipeline crawl-attachments
.venv/bin/spb-pipeline parse
.venv/bin/spb-pipeline ocr
.venv/bin/spb-pipeline parse
.venv/bin/spb-pipeline chunk --max-chars 360 --overlap-chars 50
.venv/bin/spb-pipeline report
.venv/bin/spb-pipeline embed --model moka-ai/m3e-base
~~~

`milvus-check` 只读检查；首次 `milvus-create` / `milvus-ingest` 建表/写入，
后续 `milvus-sync` 插入缺失 chunk。这些写入必须有指定目标与权限；
创建不覆盖同名 collection，首次导入要求目标为空。

产物：`data/raw/` 为目录/页面/附件，`data/state/crawl.db` 保存抓取状态，
`data/processed/` 包含 documents/chunks、OCR sidecar 和 embeddings，
`data/reports/` 保存质量报告；都不随 Git 分发。失败附件保留 lineage，不静默丢弃。

## 代码边界

| 入口 | 职责 |
| --- | --- |
| [cli.py](src/spb_pipeline/cli.py)、[pipeline.py](src/spb_pipeline/pipeline.py) | 命令与阶段编排 |
| [inventory.py](src/spb_pipeline/inventory.py)、[crawler.py](src/spb_pipeline/crawler.py)、[attachments.py](src/spb_pipeline/attachments.py) | 目录、正文与附件采集；保留来源 |
| [state.py](src/spb_pipeline/state.py) | 抓取状态与续跑，不是 Agent 会话存储 |
| [parser.py](src/spb_pipeline/parser.py)、[ocr.py](src/spb_pipeline/ocr.py)、[normalize.py](src/spb_pipeline/normalize.py) | 解析、OCR 与规范化 |
| [chunker.py](src/spb_pipeline/chunker.py)、[embedding.py](src/spb_pipeline/embedding.py) | 模型感知切分与向量产物 |
| [quality.py](src/spb_pipeline/quality.py)、[Milvus sink](src/spb_pipeline/sinks/milvus.py) | 质量报告与契约约束下的写入 |

`SPB_DATA_DIR` 可改产物根目录，默认 `data/`；CLI 不自动加载 dotenv。
原始附件、正文、OCR、向量和业务报告不入 Git，失败来源不能静默丢失。

## 验证与变更

~~~bash
.venv/bin/pytest apps/offline-pipeline/tests packages/contracts/tests
~~~

变更 chunk ID、metadata、模型、维度或字段时，先检查共享契约，再验证 RAG 可读及旧数据兼容。
不要用重建已有 collection 作为普通测试步骤。在线接口与拒答行为属于 [RAG](../rag-api/README.md)。
