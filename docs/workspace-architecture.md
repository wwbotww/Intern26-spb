# Workspace 架构与模块边界

> 当前实现基线：`offline-pipeline 0.2.0`、`rag-api 0.5.1`、
> `assistant-api 0.3.2`、`chat-web 0.2.0`、`eval 0.3.0`。本文只描述已实现边界。

## 目标

仓库同时承载离线数据生产、在线检索问答、单轮工具编排和黑盒评估工具。各应用必须
在代码、依赖、进程、权限和发布层面保持隔离；评估工具只能通过 HTTP 使用
在线 API。

```mermaid
flowchart LR
    C["packages/contracts"] --> O["apps/offline-pipeline"]
    C --> R["apps/rag-api"]
    O -->|"写入/同步"| M["Milvus spb_policy_chunks"]
    M -->|"只读检索"| R
    W["apps/chat-web"] -->|"HTTP POST + SSE"| A
    E["eval"] -->|"RAG 专项 HTTP 评估"| R
    E -->|"Assistant HTTP 评估"| A
    A["apps/assistant-api<br/>单轮工具编排"]
    A -->|"内部 HTTP"| R
    A -->|"只读 SELECT"| P["设备价格 MySQL"]
```

## Workspace 成员

### `apps/offline-pipeline`

职责：

- 抓取页面和附件；
- HTML、文档与 OCR 解析；
- 文本切分和文档 embedding；
- Milvus collection 创建、写入和增量同步；
- 数据质量报告。

它可以依赖 `spb-contracts`，但不得依赖 `spb-rag-api`。

### `apps/rag-api`

职责：

- 在线 HTTP API；
- 查询 embedding；
- Milvus Hybrid Retrieval；
- 轻量 Cross-Encoder 重排序与本地相关性门槛；
- RAG 上下文与引用；
- DeepSeek 证据充分性 Judge、答案生成和 SSE；
- 在线鉴权、限流、日志和监控。

它可以依赖 `spb-contracts`，但不得导入 `spb_pipeline`。当前包含查询
embedding、Milvus 只读 Hybrid Retrieval、RRF、结构化过滤、
`/v1/retrieve`，以及 DeepSeek grounded answer、引用和 SSE。

### `apps/assistant-api`

职责：

- 接收显式 `policy` / `device_price` 查询模式；
- 按固定映射执行且只执行一个只读工具；
- 提供统一 `ToolResult`、政策/价格 `Evidence`、JSON 和 SSE 契约；
- 提供独立鉴权、限流、健康检查和指标；
- 工具由代码静态注册，当前不允许 LLM 选择工具。

设置 `ASSISTANT_MYSQL_DSN` 时注册设备价格只读工具，使用 SQLAlchemy
Core + PyMySQL 参数化查询和 RapidFuzz 候选排序；连接会话强制只读，仓储接口
没有写方法。未配置或连接失败时，该工具 readiness 为 `not_ready`，不会用空结果
掩盖技术错误。

同时设置 `ASSISTANT_RAG_BASE_URL` 和 `ASSISTANT_RAG_API_KEY` 时注册政策工具，
通过 HTTP 调用 `rag-api` 的公开契约；它映射三类拒答原因，并校验政策回答的引用
编号和证据可追溯字段。两个能力都 ready 时整体 readiness 才为 200。

`assistant-api` 是当前 `chat-web` 的唯一 API 上游。跨应用只使用 HTTP 或数据库
适配器，不得导入其他应用实现。请求不接受对话历史，服务不保存会话。

### `apps/chat-web`

职责：

- 提供 Vue 3 单页问答界面；
- 先由用户显式选择政策或设备价格查询类别；
- 通过 POST SSE 展示状态、答案、分类证据、信息缺口和错误；
- 由开发代理或 Nginx 同源代理调用 `assistant-api`；
- 政策证据显示原文引用，价格证据显示结构化候选卡片；
- 仅保留页面内消息记录，不承担检索、生成或持久化职责。

它不加入 Python `uv` workspace，不导入任何 Python 应用，也不直连 Milvus 或
DeepSeek。当前 API 没有会话历史契约，因此界面中的连续消息仍按单轮请求处理。

### `packages/contracts`

只包含：

- collection 名称、schema 版本和字段名；
- embedding 模型、维度、归一化和 metric；
- 跨边界元数据结构。

禁止包含 HTTP、Milvus、模型加载、文件读写或业务流程代码。

### `eval`

职责：

- 加载人工标注 JSONL 评估集；
- 以真实客户端方式调用 RAG 或 Assistant 的 `/v1/chat`，以及 RAG
  `/v1/retrieve`；
- 计算 RAG 召回/门槛/引用/事实覆盖，以及 Assistant 路由、状态、证据、价格
  候选、延迟和吞吐指标；
- 离线扫描 reranker 阈值并对比 baseline/experiment；
- 生成失败样本人工复核队列；
- 生成本地 JSON、JSONL 和 Markdown 报告。

它不得导入 `spb_rag_api`、`spb_assistant_api` 或 `spb_pipeline`，不得直连
Milvus/MySQL，也不加入在线 Docker 镜像。私有评估集和运行报告默认不进入版本
控制。

## 依赖方向

```text
spb-policy-pipeline ─┐
                     ├──> spb-contracts
spb-rag-api ─────────┘
spb-assistant-api     （独立应用；HTTP 调用 RAG，只读访问价格源）
```

以下依赖均被禁止：

```text
spb-rag-api -> spb-policy-pipeline
spb-policy-pipeline -> spb-rag-api
spb-assistant-api -> spb-rag-api / spb-policy-pipeline
spb-contracts -> 任一应用
```

`apps/rag-api/tests/test_architecture.py` 会扫描各 Python 包的 import，阻止
在线、离线和 Assistant 应用相互导入，也阻止 contracts 反向依赖任一应用。

## 运行和发布边界

| 项目 | 离线流水线 | 在线 RAG API | Assistant API |
|---|---|---|---|
| 进程 | 批处理 CLI | 常驻 API | 常驻 API |
| 数据权限 | Milvus 建表/写入 | Milvus 只读 | 设备价格 MySQL 只读 |
| 本地数据目录 | 需要 | 禁止依赖 | 禁止依赖 |
| 模型 | embedding | embedding、Reranker、DeepSeek | 无模型 |
| Docker 镜像 | 未提供专用镜像 | rag-api | assistant-api |
| 发布节奏 | 数据任务 | 政策在线服务 | 单轮工具 API |

## 共享契约

当前契约：

```text
database        aisv
collection      spb_policy_chunks
schema_version  1
embedding       moka-ai/m3e-base
dimension       768
normalized      true
metric          COSINE
dense field     text_dense
sparse field    text_sparse
```

离线建表和在线启动检查都必须使用该契约。未来 schema 发生不兼容变更时，
应增加 schema version 或创建新 collection，不能让在线服务静默适配。

## 在线问答流程

```mermaid
sequenceDiagram
    participant U as "API Client"
    participant A as "rag-api"
    participant E as "m3e-base"
    participant M as "Milvus"
    participant R as "BGE reranker"
    participant D as "DeepSeek"
    U->>A: "POST /v1/chat"
    A->>E: "查询文本 embedding"
    E-->>A: "768 维归一化向量"
    A->>M: "Dense HNSW + BM25 candidates"
    M->>M: "RRF fusion"
    M-->>A: "融合候选"
    A->>R: "问题 + Top 20 candidates"
    R-->>A: "重排序分数"
    alt "没有候选通过本地门槛"
        A-->>U: "固定资料不足"
    else "本地门槛通过"
        A->>D: "JSON 证据充分性判定"
        alt "Judge 拒绝"
            A-->>U: "固定资料不足"
        else "Judge 通过"
            A->>D: "问题 + Judge 认可的编号知识上下文"
            D-->>A: "SSE answer deltas"
        end
    end
    A-->>U: "metadata / delta / usage / done"
```

客户端只能提交白名单结构化过滤字段。服务端负责构造 Milvus filter，
不接受原始表达式。Milvus 适配器仅暴露 schema 校验、collection load 和
hybrid search，没有任何数据写入方法。
