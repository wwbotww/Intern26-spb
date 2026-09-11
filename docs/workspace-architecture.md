# Workspace 架构与模块边界

本文只描述跨模块职责、依赖和复用。服务内部实现从各自 README 进入；
可用能力与部署版本见[当前状态](current-status.md)。

## 应用边界

| 模块 | 拥有的职责 | 不应承担 |
| --- | --- | --- |
| [offline-pipeline](../apps/offline-pipeline/README.md) | 抓取、解析/OCR、模型感知切分、向量化、Milvus 建表/同步 | 在线问答、会话、服务鉴权 |
| [rag-api](../apps/rag-api/README.md) | 查询 embedding、Dense/BM25 + RRF、重排、证据充分性 Judge、引用式回答 | 爬虫、OCR、业务库写入、Agent 会话 |
| [assistant-api](../apps/assistant-api/README.md) | V1 Tool、V2 Agent、理解/路由、工具适配、会话/幂等、身份与限流 | 导入 RAG 实现、生成价格事实、生产采集 |
| [chat-web](../apps/chat-web/README.md) | 同源 HTTP/SSE、输入控件、会话 UI、领域结果、临时通知 | 直接连接数据库/供应商、持有服务 Key、解释 checkpoint |
| [contracts](../packages/contracts/README.md) | collection、embedding、schema、索引元数据的数据契约 | 全项目通用 DTO、业务路由、HTTP/数据库连接 |
| [eval](../eval/README.md) | HTTP 黑盒、组件文件契约、Gold/指标/门禁、审核与报告比较 | 导入在线应用、读取 checkpoint/业务库、参与线上决策 |

Python 应用通过 uv workspace 管理；Web 独立使用 npm。架构测试检查在线/离线应用不能互相导入，
Eval 不能导入 Assistant 实现；测试装配系统后通过 ASGI HTTP 验证不属于生产依赖。

~~~text
offline-pipeline ─┐
                 ├── import → packages/contracts
rag-api ─────────┘
assistant-api ─── HTTP → rag-api
assistant-api ─── Repository → MySQL（SELECT）
chat-web ──────── HTTP → assistant-api
eval ─────────── HTTP / 文件契约 → 被测系统
~~~

## 复用在哪里发生

- 离线生产和在线检索共享数据契约，不共享爬虫或服务实现。模型/collection 不兼容时必须协调升级。
- Assistant V1/V2 复用同一政策与设备价格 Tool；工作流通过类型化 Adapter 调用，不复制检索或匹配算法。
  装配顺序、资源生命周期和 Agent 内部分层见 [Assistant](../apps/assistant-api/README.md)。
- Web 按公开 Result 类型复用 Renderer；不消费 LangGraph State，服务端 checkpoint 才是事实状态。
- Eval 独立验证公开结果；不导入业务实现来“复用答案”，避免实现错误同时污染评分。
- 设备价格 V1 与全品类 V2 的业务语义不同，不能只改 SQL 表名复用旧查询。范围见[数据源边界](data-sources.md)。
  拟通过统一查询核心与分类策略替换旧表依赖，详见[实施计划](product-price-implementation-plan.md)；
  这仍是目标架构，不改变上面所述的当前复用关系。

## 跨端契约及维护者

| 契约 | 定义 / 维护位置 | 消费者与变更约束 |
| --- | --- | --- |
| RAG collection / embedding / 元数据 | [contracts](../packages/contracts/README.md) | Pipeline 写入与 RAG 读取联合回归；不兼容变更需新版本/collection |
| RAG HTTP | [RAG API](../apps/rag-api/docs/api.md) | Assistant HTTP Adapter、独立调用方与 Eval；保持证据和拒答语义 |
| Assistant V1 HTTP | [V1 接口](../apps/assistant-api/docs/api-v1.md) | 旧 Web / 客户端 / Eval；不套用 V2 会话语义 |
| Agent V2 HTTP / SSE | Assistant DTO → [OpenAPI](openapi/assistant-agent-v2.openapi.json) | Web 生成类型和运行时校验、Eval 独立镜像同步更新 |

OpenAPI 留在根层是因为它是跨端生成物；人读的 [V2 接口说明](../apps/assistant-api/docs/api-v2.md)
由 Assistant 服务目录维护。内部 Domain、公开 DTO、Eval 镜像与 Web 校验服务于不同信任边界，
不能因字段相似就合并成同一个全局类型包。

## 改功能时从哪里开始

| 变更 | 修改边界 | 必须带上的验证 |
| --- | --- | --- |
| 新意图/槽位 | Assistant Domain → Understanding → Policy/Descriptor | 硬实体、歧义/冲突、缺槽、未装配阻断、组件与 V2 回归 |
| 新供应商 | Assistant wire contract / Gateway → 组合根 | 编码/签名/失败/语义熔断、资源关闭；真实调用单独验收 |
| 新结果字段 | Assistant Domain / 公开投影 → OpenAPI → Web TS/Renderer → Eval 镜像 | JSON/SSE、旧快照、非法事实与私有字段拒绝 |
| 新存储/扩容 | Assistant Port / adapter / coordinator → 部署 | owner、TTL、创建/消息/Tool 幂等、恢复及冲突 |
| 调整 RAG/价格 | 原 Tool / Source / Repository | V1 与 V2 共用回归；不在 Node 复制业务算法 |

设计理由见[ADR 索引](adr/README.md)；安装、联调与全局验证见[开发指南](development.md)。
