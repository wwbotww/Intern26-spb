# LangGraph 查询助手与政策 RAG

一个可恢复、可观测的只读查询 Agent：用 LangGraph 编排理解、澄清、补槽和工具执行，
以政策知识库和结构化价格库验证业务边界。保留独立的 V1 单轮 API，不让模型直接决定价格、
拼接 SQL 或自由调用工具。

**单实例内网 Agent 已阶段性完成。** 政策 RAG、设备与已有生鲜价格已接入真实数据并限定发布；
轨迹、资费、时限接口作为遗留保留，正式入口正常显示暂不可用。
“全品类”不代表任意商品覆盖，本地合成通路也不等于供应商互通；完整边界见[当前状态](docs/current-status.md)。

## 架构与能力

~~~text
公开文档 → offline-pipeline → Milvus ← rag-api
                                         ↑ HTTP
浏览器 → 同源代理 → assistant-api / LangGraph
                         ├→ 复用政策 Tool ┘
                         ├→ 商品价格 Tool → 统一查询核心 → MySQL V2（只读）
                         └→ 类型化物流 Gateway（按装配启用）

eval → 公开 HTTP / 版本化组件文件 → 独立评分与回归门禁
~~~

- Understanding：显式入口/规则优先，DeepSeek 可选 fallback，硬实体重新验证。
- Workflow：条件路由、interrupt/resume、冲突确认、有界重试、SQLite 和执行收据。
- API/Web：版本化 JSON/SSE、匿名访客隔离、刷新恢复、幂等重放、领域卡片与引用。
- 价格：设备/生鲜分类策略、结构化候选选择、金额/单位/来源证据；V1 设备协议借用同一核心。
- 工程保障：低基数指标、脱敏语义 Trace、逐 Node OTel、黑盒 Eval、备份恢复和离线 CI。
- RAG：Dense + BM25 / RRF、重排、证据充分性判断及引用式生成；离线数据生产独立运行。

[模块边界](docs/workspace-architecture.md) · [数据源](docs/data-sources.md) ·
[遗留与可选路线图](docs/roadmap.md) · [面试复盘](docs/project-retrospective.md)

## 快速开始

要求 Python 3.11+、uv、Node `^22.12.0 || >=24.0.0`；CI 使用 Python 3.12 / Node 22。
安装会访问软件仓库，但不调用业务或付费模型：

~~~bash
uv sync --frozen --all-packages --group dev --python 3.12
npm --prefix apps/chat-web ci --include=dev --engine-strict
~~~

第一次接手先按[开发指南](docs/development.md)运行无 Key 的合成通路，再连接批准的真实依赖。
Web 默认仍是 V1，Agent UI 是构建时显式选择；代码默认值不等于服务器部署配置。
不要复制覆盖已有 `.env`，合成入口禁止对外部署。

## 文档分两层，按职责阅读

根目录讲全局，服务目录讲自身。每个服务的 README 是其运行、接口与内部设计入口；
根 `docs/` 不再收纳各服务的完整说明书。

| 模块入口 | 职责 |
| --- | --- |
| [offline-pipeline](apps/offline-pipeline/README.md) | 抓取、解析/OCR、切分、向量化和 Milvus 写入 |
| [rag-api](apps/rag-api/README.md) | 只读检索、证据判断、问答与引用 |
| [assistant-api](apps/assistant-api/README.md) | V1 Tool、LangGraph Agent、适配器、身份和持久化 |
| [chat-web](apps/chat-web/README.md) | Vue 客户端、SSE、会话 UI 和领域 Renderer |
| [contracts](packages/contracts/README.md) | 离线写入与 RAG 读取的数据契约；不是通用 DTO 仓库 |
| [eval](eval/README.md) | 独立黑盒与组件评测、数据冻结、门禁及对比 |

- 项目交接：[文档导航](docs/README.md)、[跨服务运维](docs/operations.md)、[当前状态](docs/current-status.md)。
- 部署执行：[Agent 操作手册](deploy/agent/README.md)、[RAG/V1 部署基线](docs/deployment.md)。
- 设计与证据：[ADR](docs/adr/README.md)、[压缩交付摘要](docs/history/agent-delivery-summary.md)。
- 跨端契约：[Agent OpenAPI](docs/openapi/assistant-agent-v2.openapi.json)保留在根层，由服务端维护，Web/Eval 消费。

仓库不包含原始业务数据、私有评估集、完整业务运行报告或真实凭据。
HTTP 内网例外、单实例、代表性质量与真实物流缺口均有明确边界，不宣称企业级生产 SLA。
