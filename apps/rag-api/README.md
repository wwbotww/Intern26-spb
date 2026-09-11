# RAG API

[项目全貌](../../README.md) · [全局模块边界](../../docs/workspace-architecture.md) · [当前状态](../../docs/current-status.md)

独立、无会话的政策知识服务，只读 Milvus：查询向量 → Dense/BM25 + RRF → 重排 →
证据充分性判断 → 引用式回答。Assistant 通过 HTTP 复用它；本服务不导入离线流水线，
不管理 Agent 会话，也不读取设备价格数据库。

## 接手入口

- 调用字段、JSON/SSE、鉴权与错误：[API 使用文档](docs/api.md)。
- 检索门槛、Judge、grounding 与模型行为：[问答机制](docs/design.md)。
- 离线生产/在线读取的 schema：[共享数据契约](../../packages/contracts/README.md)。
- 数据生产者、更新与覆盖：[全局数据源](../../docs/data-sources.md)。

## 配置与启动

命令从仓库根目录执行，先完成[工作区安装](../../docs/development.md)。
对照 [.env.example](.env.example)配置已有私有文件，不要覆盖它或输出凭据。

| 必需依赖 / 配置 | 边界 |
| --- | --- |
| `RAG_MILVUS_URI / TOKEN` | 已存在且符合共享契约的 collection；在线只读，不建表/补写 |
| `RAG_API_KEYS` | 调用本服务的服务 Key；不是模型 Key |
| `RAG_DEEPSEEK_API_KEY` | 服务端 Judge/生成使用；与 Assistant 的意图理解 Key 分离 |
| embedding / reranker 权重 | 本地可加载；首次启动可能下载权重，不属于无网络 Demo |

[ApiSettings](src/spb_rag_api/settings.py)依次读取 `apps/rag-api/.env`、根 `.env`；
后者同名值覆盖前者，进程环境优先。确认访问目标与权限后启动：

~~~bash
RAG_HOST=127.0.0.1 RAG_PORT=8080 .venv/bin/spb-rag-api
~~~

`/v1/retrieve` 不调用 DeepSeek；有候选的 `/v1/chat` 可能产生模型用量。
未配置 DeepSeek 时，纯检索仍可用，但整体 readiness 为 503，问答返回
`chat_provider_unavailable`。进程 live 不代表数据就绪或问题可回答。

单测不需要上述真实依赖；只想验证 Agent 通路时使用
[Assistant 合成入口](../assistant-api/docs/local-development.md)，不必启动 RAG。
多服务容器配置归[部署基线](../../docs/deployment.md)，不要据此覆盖已有 Agent 发布。

## 内部边界

| 代码入口 | 职责 |
| --- | --- |
| [domain](src/spb_rag_api/domain/) | 知识片段、结果与 Port；不混入 HTTP/SDK |
| [services](src/spb_rag_api/services/) | 检索、重排、证据门槛与回答流程 |
| [adapters](src/spb_rag_api/adapters/) | Milvus、embedding、reranker、DeepSeek；不生成业务路由 |
| [api](src/spb_rag_api/api/) | 公开 DTO、路由与 JSON/SSE 投影 |
| [middleware](src/spb_rag_api/middleware/)、[security](src/spb_rag_api/security/) | 服务鉴权、限流、请求预算 |
| [observability](src/spb_rag_api/observability/) | 日志、请求上下文、指标；不记录秘密或完整问题 |

相关性高不等于证据足以回答。保留 no_context / reranker_rejected / llm_rejected 区别；
模型合同错误不能伪装成资料不足。具体门槛只在问答机制和设置中维护。

## 验证与变更

~~~bash
.venv/bin/pytest apps/rag-api/tests packages/contracts/tests
~~~

改检索/拒答/引用后回归 Assistant V1/V2 的共享政策 Tool 与 [Eval](../../eval/README.md)；
改 collection/embedding 时同时验 Pipeline 写入和已有数据兼容，不能只改本服务常量。
真实质量评测需要批准的数据与模型预算，不能用单测通过替代。
