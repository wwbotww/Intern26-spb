# 文档 RAG 与只读工具编排 Demo

本仓库用于验证公开文档数据处理、检索增强问答、结构化只读查询和统一 API
编排等技术能力。当前示例数据包括国家邮政局“政策法规标准”栏目及一个设备价格
数据源；它们用于约束和检验各模块，不代表对完整真实业务流程的建模。

仓库包含相互隔离的离线数据流水线、在线 RAG API、Assistant、Web、黑盒评估
工具和共享数据契约。当前定位是 Demo、测试与技术方向验证，场景名称、交互方式
和后续能力都可能调整；文档只陈述现有代码直接支持的行为。仓库不包含原始业务
数据、私有评估集、运行报告或任何真实凭证。

## 核心能力

| 能力 | 实现 |
|---|---|
| 数据发现 | 按栏目接口分页获取完整目录，保留外链与失败状态 |
| 多格式解析 | HTML、PDF、DOCX、旧版 Word、XLSX、图片与扫描 PDF |
| OCR | macOS Vision 中文 OCR，按附件和页码保存可续跑 sidecar |
| 结构化切分 | 识别法规章、节、条、段落和表格行，保留章节路径 |
| 向量化 | `moka-ai/m3e-base`，768 维、L2 归一化 |
| 混合检索 | Milvus HNSW/COSINE Dense + BM25 Sparse + RRF |
| 语义重排 | `BAAI/bge-reranker-base` Cross-Encoder |
| 可信问答 | 本地相关性门槛 + DeepSeek 证据充分性门槛 |
| 可追溯输出 | 回答包含编号引用、文档元数据和原文链接 |
| 服务接口 | JSON 检索、JSON 问答、SSE 流式问答、健康检查和指标 |
| 单轮工具编排 | Web 显式二选一、单轮单工具调用、分类证据和信息缺口展示 |
| 黑盒评估 | RAG 召回/门槛，以及 Assistant 路由、状态、证据、价格候选和效率 |

## 架构

```mermaid
flowchart LR
    S["国家邮政局栏目与附件"] --> O["offline-pipeline"]
    O -->|"抓取 / 解析 / OCR / 切分 / 向量化"| M["Milvus<br/>spb_policy_chunks"]
    C["packages/contracts"] --> O
    C --> R["rag-api"]
    M -->|"只读 Hybrid Search"| R
    R --> B["Reranker"]
    B --> J["DeepSeek Judge"]
    J --> G["带引用回答 / 明确拒答"]
    W["chat-web"] -->|"POST + SSE"| A
    E["eval"] -->|"RAG 专项评估"| R
    E -->|"统一助手评估"| A
    A["assistant-api<br/>单轮工具编排"]
    A -->|"内部 HTTP"| R
    A -->|"只读 SELECT"| Q["设备价格 MySQL"]
```

离线流水线拥有目标 collection 的创建和写入能力；在线服务只读访问 Milvus，
不包含爬虫、OCR 或数据库写入代码；评估工具只通过 HTTP 观察在线服务，不
导入在线实现，也不直连数据库。

## 仓库结构

```text
apps/
  offline-pipeline/   抓取、解析、OCR、切分、向量化和 Milvus 同步
  rag-api/            在线检索、双重相关性门槛和 DeepSeek 问答
  assistant-api/      显式查询模式、单工具分发和统一响应
  chat-web/           Vue 3 模式选择、查询与分类证据展示界面
packages/
  contracts/          collection schema、embedding 与元数据共享契约
eval/                 独立 HTTP 黑盒评估工具
deploy/               Docker Compose 与 Prometheus 配置
docs/                 API、架构和部署文档
```

依赖方向固定为：

```text
spb-policy-pipeline ─┐
                     ├──> spb-contracts
spb-rag-api ─────────┘
spb-assistant-api     （不导入其他应用，仅通过 HTTP/数据库边界连接）
```

在线和离线应用禁止相互导入。架构测试会检查该约束。

## 共享数据契约

| 项目 | 当前值 |
|---|---|
| Milvus database | `aisv` |
| Collection | `spb_policy_chunks` |
| Schema version | `1` |
| Embedding | `moka-ai/m3e-base` |
| Dimension | `768` |
| Dense metric | `COSINE` |
| Dense field | `text_dense` |
| Sparse field | `text_sparse` |

离线建表和在线启动都会校验该契约。不兼容的 schema 变更应升级 schema version
或创建新 collection，不能让在线服务静默兼容。

## 环境要求

- Python 3.11+（容器当前使用 3.12）；
- [uv](https://docs.astral.sh/uv/)；
- Milvus 2.6.x；
- Docker 与 Docker Compose（容器部署时）；
- DeepSeek API Key（使用问答接口时）；
- Poppler `pdftoppm`（处理扫描 PDF 时）；
- macOS Vision（使用当前 OCR 实现时）。

安装整个 workspace：

```bash
uv sync --python 3.11 --all-packages --all-extras --group dev
```

`--all-extras` 会安装 embedding 和 Milvus 依赖，首次安装需要下载 PyTorch 与
模型相关运行库。

## 离线数据流水线

### 配置

```bash
cp apps/offline-pipeline/.env.example apps/offline-pipeline/.env

set -a
source apps/offline-pipeline/.env
set +a
```

离线 CLI 直接读取进程环境变量，因此运行命令前需要导入该文件，或在任务调度器
中配置等价环境变量。`.env`、原始文件、处理产物和向量文件均不会进入版本
控制。

### 完整处理流程

```bash
# 1. 获取栏目目录并抓取详情页
uv run --package spb-policy-pipeline spb-pipeline inventory
uv run --package spb-policy-pipeline spb-pipeline crawl-details

# 2. 解析页面、发现并下载附件
uv run --package spb-policy-pipeline spb-pipeline parse
uv run --package spb-policy-pipeline spb-pipeline crawl-attachments
uv run --package spb-policy-pipeline spb-pipeline parse

# 3. 对扫描附件执行 OCR，然后重新解析
uv run --package spb-policy-pipeline spb-pipeline ocr
uv run --package spb-policy-pipeline spb-pipeline parse

# 4. 按模型输入约束切分、检查质量并生成向量
uv run --package spb-policy-pipeline spb-pipeline \
  chunk --max-chars 360 --overlap-chars 50
uv run --package spb-policy-pipeline spb-pipeline report
uv run --package spb-policy-pipeline spb-pipeline \
  embed --model moka-ai/m3e-base
```

抓取状态保存在 `data/state/crawl.db`。命令可以重复执行；已成功下载且本地文件
仍存在的资源默认不会重复请求。

### Milvus 初始化与同步

首次创建独立 collection 并写入：

```bash
uv run --package spb-policy-pipeline spb-pipeline milvus-create
uv run --package spb-policy-pipeline spb-pipeline milvus-ingest
```

后续只插入数据库中缺失的 chunk：

```bash
uv run --package spb-policy-pipeline spb-pipeline milvus-sync
```

只读检查 schema、记录数、加载状态和索引状态：

```bash
uv run --package spb-policy-pipeline spb-pipeline milvus-check
```

连接参数默认从 `apps/offline-pipeline/.env` 或环境变量读取，也可以显式传入
`--uri`、`--database`、`--collection` 和 `--token`。创建逻辑不会覆盖已存在
的同名 collection；首次导入要求目标 collection 为空。

### 本地产物

```text
data/raw/inventory/              栏目接口原始响应
data/raw/html/                   详情页原始 HTML
data/raw/attachments/            原始附件
data/state/crawl.db              抓取状态与错误记录
data/processed/inventory.jsonl   规范化目录
data/processed/attachments.jsonl 附件清单与父文档关系
data/processed/documents.jsonl   结构化文档
data/processed/chunks.jsonl      检索与 embedding 片段
data/processed/ocr/              OCR sidecar
data/processed/embeddings-*.npz  Dense 向量与稳定 chunk ID
data/reports/quality-report.json 数据质量报告
```

## 在线 RAG API

在线服务提供：

| 方法 | 路径 | 用途 |
|---|---|---|
| `POST` | `/v1/retrieve` | 混合检索与重排序 |
| `POST` | `/v1/chat` | 双重门槛后的 JSON/SSE 引用式问答 |
| `GET` | `/v1/auth/check` | 受保护的服务间 Key 轻量校验 |
| `GET` | `/health/live` | 进程存活检查 |
| `GET` | `/health/ready` | 检索、重排、Milvus、DeepSeek、Judge 和服务配置检查 |
| `GET` | `/metrics` | Prometheus 指标 |

问答流程：

1. m3e-base 生成查询向量；
2. Milvus Dense 与 BM25 各自召回候选，RRF 融合；
3. BGE Reranker 重排并执行第一道相关性过滤；
4. DeepSeek Judge 判断剩余证据是否足以回答；
5. 只使用 Judge 认可的来源生成答案和引用；
6. 任一道门槛拒绝时返回固定的“资料不足”结果。

### Docker 启动

```bash
cp apps/rag-api/.env.example apps/rag-api/.env
# 填写 RAG_MILVUS_URI、RAG_MILVUS_TOKEN、
# RAG_DEEPSEEK_API_KEY 和 RAG_API_KEYS

docker compose -f deploy/docker-compose.yml build rag-api
docker compose -f deploy/docker-compose.yml up -d rag-api
docker compose -f deploy/docker-compose.yml ps

curl http://127.0.0.1:8080/health/live
curl http://127.0.0.1:8080/health/ready
```

镜像在构建阶段包含 embedding 和 reranker 权重，运行时可在无公网模型仓库的
内网环境启动。在线容器以非 root 用户运行，并使用只读根文件系统。

### 调用示例

```bash
export SPB_RAG_BASE_URL=http://127.0.0.1:8080
export SPB_RAG_API_KEY=your-service-api-key

curl -sS -X POST "${SPB_RAG_BASE_URL}/v1/retrieve" \
  -H "Authorization: Bearer ${SPB_RAG_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "快递业务经营许可需要符合哪些条件？",
    "top_k": 5,
    "candidate_k": 40
  }'

curl -N -X POST "${SPB_RAG_BASE_URL}/v1/chat" \
  -H "Authorization: Bearer ${SPB_RAG_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "快递业务经营许可需要符合哪些条件？",
    "stream": true,
    "top_k": 5
  }'
```

服务 API Key 与 DeepSeek API Key 相互独立。不要把任何真实凭证写入命令、
README、日志或提交记录。

## Assistant 单轮工具编排

`apps/assistant-api` 已建立独立 FastAPI 服务和固定分发契约：客户端必须显式提交
`policy` 或 `device_price`，每个请求只执行对应的一个工具。请求 schema 禁止
`history`、`messages` 等额外字段，服务端不保存会话。

设备价格模式通过 MySQL 只读查询：使用参数化 SQL 缩小候选，以 RapidFuzz
进行型号排序，并按容量或内存规格过滤；有歧义时返回多个 SKU，且每条价格都包含
来源、观察时间和数据库标识。仓储连接建立时把会话设置为只读，代码中不包含任何
价格写入或 DDL。

政策模式通过内部 HTTP 调用现有 `rag-api`，映射本地 Reranker、DeepSeek Judge
的三类拒答状态，并在返回前再次校验引用编号、chunk、原文 URL 和证据类型。成功
或部分成功但没有证据的工具结果会被拒绝；同时询问政策和价格的问题会提示拆成
两次独立请求，不会触发两个工具。

同时配置可用的 RAG 地址/服务 Key 和 MySQL DSN 后，两个工具均 ready，整体
`/health/ready` 才返回 200。任一配置缺失或依赖不可用时保持 503。`chat-web`
已通过同源代理使用该入口，不再直接调用 `rag-api`。

本地启动框架服务：

```bash
cp apps/assistant-api/.env.example apps/assistant-api/.env
# 设置 ASSISTANT_API_KEYS、ASSISTANT_RAG_API_KEY 和只读 MySQL DSN
uv run --package spb-assistant-api spb-assistant-api
```

设备价格请求示例：

```json
{
  "mode": "device_price",
  "question": "某品牌某型号 256GB 的参考价格是多少？",
  "stream": true
}
```

支持 `/v1/chat`、`/health/live`、`/health/ready` 和 `/metrics`；SSE 事件为
`status`、`evidence`、`delta`、`usage`、`done` 或 `error`。设备证据包含当前价、
可选原价、币种、在售状态、来源、观察时间、产品/SKU 标识和请求内匹配分数；
政策证据包含文档/chunk 标识、原文链接、文号、机构、章节和检索分数。
`finish_reason` 表示统一流程状态，`reason_code` 保留具体拒答或拆分原因。所有价格
字段都是数据源中的参考信息，不代表交易、估值或结算结果。

## 问答用户界面

`apps/chat-web` 是独立的 Vue 3 查询界面，通过同源 `/api` 代理调用
`assistant-api`。首屏要求用户选择“政策查询”或“设备价格”，请求固定携带该模式
和当前问题，不发送页面历史。界面分别展示政策引用和设备价格候选卡片，并区分
部分结果、信息不足、无匹配和技术错误。服务 API Key 只由 Vite 开发代理或
Nginx 容器代理读取，不进入浏览器构建产物。

本地开发：

```bash
cp apps/chat-web/.env.example apps/chat-web/.env
# 将 CHAT_WEB_ASSISTANT_API_KEY 设置为 ASSISTANT_API_KEYS 中的一项

cd apps/chat-web
npm ci
npm run dev
```

浏览器访问 `http://127.0.0.1:3000`。开发代理默认连接
`http://127.0.0.1:8081`，因此应先启动并配置好 `assistant-api`。

Docker 启动 API 和界面：

```bash
docker compose --env-file /path/to/untracked/demo.env \
  -f deploy/docker-compose.yml build rag-api assistant-api chat-web
docker compose --env-file /path/to/untracked/demo.env \
  -f deploy/docker-compose.yml up -d rag-api assistant-api chat-web
```

RAG 配置由未跟踪的 `apps/rag-api/.env` 提供；`demo.env` 提供 Assistant、MySQL
和 Web 代理所需的 Compose 插值，其中 `CHAT_WEB_ASSISTANT_API_KEY` 应是
`ASSISTANT_API_KEYS` 中的一项。容器模式下浏览器同样访问
`http://127.0.0.1:3000`。页面可以保留本次打开后的消息供阅读，但每个问题在
服务端仍是独立单轮问答，切换类别也不会携带上一轮上下文。

当前通用 Compose 拓扑、运行时配置、健康检查和安全边界见
[`docs/deployment.md`](docs/deployment.md)。仓库文档不保存具体服务器、组织或
内部网络信息。

## 黑盒评估

`eval/` 支持：

- Recall@K、MRR@K 和 Gold 文档存活率；
- 可回答问题错误拒答率和无答案问题错误回答率；
- `no_context`、`reranker_rejected`、`llm_rejected` 路由分布；
- 引用 Gold 命中率与必要事实覆盖率；
- 检索和问答 P50/P95 延迟、API 错误与生成 Token；
- shadow-mode Reranker 阈值扫描；
- baseline/experiment 指标和逐样本对比；
- 自动生成 `review-queue.md` 人工复核队列；
- Assistant 固定分发、状态、证据类型/完整性和拒答证据泄露检查；
- 设备候选 Product/SKU Recall，以及 5 并发延迟、吞吐和错误基线。

运行完整评估：

```bash
export EVAL_BASE_URL=http://127.0.0.1:8080
export EVAL_API_KEY=your-service-api-key

uv run --package spb-eval spb-eval run \
  --dataset eval/datasets/private/core.jsonl \
  --mode all \
  --top-k 5 \
  --candidate-k 40 \
  --concurrency 5 \
  --label full-chain
```

运行统一 Assistant 评估：

```bash
export EVAL_ASSISTANT_BASE_URL=http://127.0.0.1:8081
export EVAL_ASSISTANT_API_KEY=your-assistant-service-key

uv run --package spb-eval spb-eval assistant-run \
  --dataset eval/datasets/private/assistant-core.jsonl \
  --concurrency 5 \
  --label assistant-full-chain
```

评估集放入 `eval/datasets/private/`，报告写入 `eval/reports/`。两者均默认被
Git 忽略，因为其中可能包含业务问题和模型回答。

完整的数据格式、指标口径、阈值扫描和实验对比命令见
[`eval/README.md`](eval/README.md)。

## 测试

运行全部测试：

```bash
uv run pytest
```

只运行某个 workspace 包：

```bash
uv run pytest apps/offline-pipeline/tests
uv run pytest apps/rag-api/tests
uv run pytest apps/assistant-api/tests
uv run pytest eval/tests
uv run pytest packages/contracts/tests
```

## 文档

- [文档导航与状态](docs/README.md)
- [Workspace 架构与模块边界](docs/workspace-architecture.md)
- [中国邮政 Assistant API 使用文档](docs/assistant-api.md)
- [API 调用方使用文档](docs/api-reference.md)
- [在线检索问答实现与 Grounding](docs/rag-api.md)
- [Linux / Docker 部署运维](docs/deployment.md)
- [LangGraph Stateful Agent Workflow 下一阶段实施方案](docs/agent-workflow-implementation-plan.md)
- [AI 应用 / Agent 求职项目复盘](docs/project-retrospective.md)
- [Eval 数据格式与指标口径](eval/README.md)

## 数据与版本控制边界

- `data/` 下的抓取数据、附件、OCR、向量和质量报告不进入 Git；
- `eval/datasets/private/` 和 `eval/reports/` 不进入 Git；
- `.env`、API Key、Milvus Token 和本地模型缓存不进入 Git；
- 仓库只提供 RAG 与 Assistant 评估集模板，不提供私有样本；
- 原始标准附件仅用于获授权的内部管理与检索，不应随代码仓库分发；
- 未明确标注有效性的历史文件统一记为 `unknown`，应用层应提示用户核验最新
  正式文件。

## 当前边界

- 当前 OCR 实现依赖 macOS Vision；迁移到 Linux 数据流水线时需要替换 OCR
  和旧 Word 转换组件；
- `milvus-sync` 只插入缺失 chunk，文档更新后的旧版本清理仍需独立版本策略；
- Reranker 默认阈值是 Demo 初始值，应在代表性正例和困难负例上重新标定；
- CPU Reranker 在并发请求下可能形成排队，部署前应基于目标硬件进行吞吐和
  P95 延迟测试；
- `assistant-api` 已接入政策 HTTP 工具和设备价格只读工具；Web 已切换为统一入口，
  并启用政策/设备价格二选一、单轮请求和分类证据展示；
- 当前 Assistant 不做自动意图识别、不保存服务端会话，也不执行多工具循环；
  仓库暂不定义未确认的后续业务场景；
- 设备价格匹配已加入品牌、系列、型号和容量等硬约束，但阈值与展示上限仍需用
  更大的代表性数据集持续校准；
- 问答结果用于政策信息辅助检索，涉及行政决定或法律结论时仍应核验主管部门
  最新正式文件。
