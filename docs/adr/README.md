# Agent Workflow 架构决策记录

本目录保留已接受决策及其当时理由，也收录标明 `Proposed` 的待采纳提议，不是实施状态表。
`Proposed` 不表示已实施；`Accepted` 表示决策获采用，
不保证全部业务范围已验收；当前能力见[状态页](../current-status.md)。
正文中的阶段测试/限制保留为历史，后续补充或取代以新 ADR 为准。

已有决策统一保留在根层，避免拆散 API / Web / Eval / 存储 / 部署共同依赖的编号链。
当前内部实现归 [Assistant 服务文档](../../apps/assistant-api/README.md)，ADR 不复制运行手册。

| ADR | 状态 | 决策 |
| --- | --- | --- |
| [0001](0001-constrained-single-agent.md) | Accepted | 采用受约束单 Agent，而非自由 ReAct / 多 Agent |
| [0002](0002-langgraph-workflow-runtime.md) | Accepted | LangGraph 作为唯一 Workflow Runtime |
| [0003](0003-hybrid-query-understanding.md) | Accepted | Query Understanding 使用 Hybrid Pipeline |
| [0004](0004-memory-and-checkpoint-boundaries.md) | Accepted | Checkpointer、元数据、RAG 与长期记忆分离 |
| [0005](0005-failure-taxonomy.md) | Accepted | Failure 作为显式状态和稳定契约处理 |
| [0006](0006-typed-tool-routing.md) | Accepted | 类型化 Command、白名单 Registry 和结果校验 |
| [0007](0007-v1-v2-api-compatibility.md) | Accepted | V2 显式装配并与稳定 V1 并行演进 |
| [0008](0008-agent-evaluation-gates.md) | Accepted | 以公开多轮黑盒行为建立 Agent 质量门禁 |
| [0009](0009-semantic-workflow-trace-and-agent-comparison.md) | Accepted | 用脱敏语义 Trace 与同样本 Agent 对比定位回归 |
| [0010](0010-understanding-component-evaluation.md) | Accepted | 将 Understanding 文件契约评测与 V2 Workflow 黑盒验收分层；补充 0008 |
| [0011](0011-node-telemetry.md) | Accepted | 语义 Trace 与真实 Node wall-clock 分层；组合根管理采样/导出，不把遥测写入 State |
| [0012](0012-query-scoped-tool-receipts.md) | Accepted | 执行收据按逻辑查询隔离；参数指纹用于完整性，重放保护不等于业务缓存 |
| [0013](0013-controlled-tracking-composition.md) | Accepted | 默认关闭的受控物流装配；单次语义熔断、生命周期归属和公开来源白名单 |
| [0014](0014-explicit-postage-pricing-context.md) | Accepted | 产品 / 地区可执行性与报价上下文先于参数指纹；配置漂移要求重启，旧完成结果保留未知依据 |
| [0015](0015-provisional-postage-adapter.md) | Accepted | 具名临时合同绑定上下文，双层签名 / 分层校验 / 单次语义熔断；离线通路与真实互通分别验收 |
| [0016](0016-command-bound-postage-review.md) | Accepted | 命令绑定的报价确认、计价主体语义引用、独立公开报价依据及多消费者契约演进 |
| [0017](0017-browser-visitor-identity.md) | Accepted | 分离代理服务鉴权、访客 owner 与共享配额；核验后恢复本地历史，普通核验不重发 Cookie |
| [0018](0018-quiescent-agent-storage-snapshots.md) | Accepted | 停服整库快照保存 owner / TTL / 幂等 / checkpoint；合作进程租约、新目录恢复、验证后发布，拒绝自动修复未完成 claim |
| [0019](0019-controlled-deployment-and-offline-ci.md) | Accepted | 分离受控入口与合成 CI、绑定代理 / Web 模式、HTTPS 路由门禁、新卷恢复与不降级数据库的 V1 回退 |
| [0020](0020-unified-product-price-query.md) | Accepted | 统一 V2 只读价格核心与分类策略、候选循环、旧协议/历史边界及无 V1 fallback 的设备能力替换 |

修改已接受决策时新增 ADR 并标记旧记录为 `Superseded`，不要静默重写历史理由。
