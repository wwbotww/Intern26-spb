# ADR-0004：区分 Checkpoint、元数据、RAG 与长期记忆

- 状态：Accepted / Phase-4A local API lifecycle verified
- 日期：2026-09-03

## 背景

“记忆”可能同时指当前补槽状态、会话归属、幂等记录、政策知识或跨会话用户偏好。
把它们放入一个 Conversation Store 会造成重复事实源、权限混淆和难以清理的敏感数据。

## 决策

| 数据 | 事实源 | 生命周期 |
| --- | --- | --- |
| 当前意图、槽位、预算、工具结果摘要 | LangGraph Checkpointer | 单 thread、短期 |
| owner、TTL、创建与消息幂等收据 | Metadata / Idempotency Repository | 会话生命周期 |
| Tool 副作用执行收据 | Tool Execution Repository | 按幂等和审计策略 |
| 政策文档与向量 | 现有 RAG / Milvus | 独立知识生命周期 |
| 用户偏好与长期事实 | 暂不建设 | 未获授权不得保存 |

`conversation_id` 经归属校验后稳定映射为 `thread_id`。Graph State 是 checkpointer 中的
唯一工作状态，不再复制到自研全量 ConversationStore。

Graph State 只保存 JSON-native、最小必要的数据。Phase-0 `InMemorySaver` 显式使用
`JsonPlusSerializer(allowed_msgpack_modules=None)` 阻止未注册 Python 类型反序列化；
自定义枚举或模型进入 checkpoint 前必须投影为稳定值。

## 结果

- 避免 Graph State 和应用 Store 的双写不一致；
- 可以独立制定知识更新、会话 TTL、幂等和 PII 策略；
- 删除会话时必须同时清理 checkpoint、元数据和相关收据；
- 阶段 2 已补充本地持久化 checkpointer、schema migration 和过期清理测试。

Phase 1 增加了与 Checkpointer 分离的内存 Tool Execution Repository，并通过历史
checkpoint 分支重放证明成功工具结果可被复用。该验证不代表跨进程恢复、并发唯一约束
或 TTL 已实现。

Phase 2 增加 `AsyncSqliteSaver`、SQLite Metadata/Idempotency Repository 和持久化 Tool
Execution Repository，并用关闭连接、重新编译 Graph 后的 interrupt/resume 测试验证
本地重启恢复。应用层会话锁、幂等 request hash、TTL Janitor 和 v1 -> v2 state migration
均有合同测试。SQLite 仍只用于单进程 Demo；跨进程串行化和生产后端属于后续部署阶段。

Phase 4A 增加会话创建收据，把 `(owner_id, Idempotency-Key, request_hash)` 与服务端随机
`conversation_id` 原子绑定，补上首次请求还没有 conversation ID 时的重放缺口；V2
删除同时清理 checkpoint、消息/Tool 收据并保留 deleted tombstone 和创建绑定，避免旧
创建请求复活已删除 Workflow。生产保留期和异步清理队列仍需隐私评审。

## 参考

- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [Checkpointer integrations](https://docs.langchain.com/oss/python/integrations/checkpointers)
