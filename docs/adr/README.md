# Agent Workflow 架构决策记录

本目录记录下一阶段 Stateful Agent 的关键架构决策。`Accepted` 表示方案已确定，
不代表所有阶段均已实现；实际能力仍以代码、测试和实施方案状态为准。

| ADR | 状态 | 决策 |
| --- | --- | --- |
| [0001](0001-constrained-single-agent.md) | Accepted / Kernel verified | 采用受约束单 Agent，而非自由 ReAct / 多 Agent |
| [0002](0002-langgraph-workflow-runtime.md) | Accepted / Kernel verified | LangGraph 作为唯一 Workflow Runtime |
| [0003](0003-hybrid-query-understanding.md) | Accepted / Hybrid verified | Query Understanding 使用 Hybrid Pipeline |
| [0004](0004-memory-and-checkpoint-boundaries.md) | Accepted / Phase 4A API lifecycle verified | Checkpointer、元数据、RAG 与长期记忆分离 |
| [0005](0005-failure-taxonomy.md) | Accepted / Phase 4A HTTP mapping verified | Failure 作为显式状态和稳定契约处理 |
| [0006](0006-typed-tool-routing.md) | Accepted / Five-query-tool path verified | 类型化 Command、白名单 Registry 和结果校验 |
| [0007](0007-v1-v2-api-compatibility.md) | Accepted / Phase 4D shared-tool verified | V2 显式装配并与稳定 V1 并行演进 |
| [0008](0008-agent-evaluation-gates.md) | Accepted / Phase 5A local baseline verified | 以公开多轮黑盒行为建立 Agent 质量门禁 |
| [0009](0009-semantic-workflow-trace-and-agent-comparison.md) | Accepted / Phase 5B local reliability verified | 用脱敏语义 Trace 与同样本 Agent 对比定位回归 |
| [0010](0010-understanding-component-evaluation.md) | Accepted / Phase 5C implemented | 将 Understanding 文件契约评测与 V2 Workflow 黑盒验收分层；补充 0008 |
| [0011](0011-node-telemetry.md) | Accepted / Phase 5E local verified | 语义 Trace 与真实 Node wall-clock 分层；组合根管理采样/导出，不把遥测写入 State |
| [0012](0012-query-scoped-tool-receipts.md) | Accepted / T2 local verified | 执行收据按逻辑查询隔离；参数指纹用于完整性，重放保护不等于业务缓存 |
| [0013](0013-controlled-tracking-composition.md) | Accepted / T3 local verified | 默认关闭的受控物流装配；单次语义熔断、生命周期归属和公开来源白名单 |
| [0014](0014-explicit-postage-pricing-context.md) | Accepted / P1 local verified | 产品 / 地区可执行性与报价上下文先于参数指纹；配置漂移要求重启，旧完成结果保留未知依据 |
| [0015](0015-provisional-postage-adapter.md) | Accepted / P2 offline verified | 具名临时合同绑定上下文，双层签名 / 分层校验 / 单次语义熔断；离线通路与真实互通分别验收 |
| [0016](0016-command-bound-postage-review.md) | Accepted / P3 offline verified | 命令绑定的报价确认、计价主体语义引用、独立公开报价依据及多消费者契约演进 |
| [0017](0017-browser-visitor-identity.md) | Accepted / 6A-1 local verified | 分离代理服务鉴权、访客 owner 与共享配额；核验后恢复本地历史，普通核验不重发 Cookie |
| [0018](0018-quiescent-agent-storage-snapshots.md) | Accepted / 6A-2 local verified | 停服整库快照保存 owner / TTL / 幂等 / checkpoint；合作进程租约、新目录恢复、验证后发布，拒绝自动修复未完成 claim |
| [0019](0019-controlled-deployment-and-offline-ci.md) | Accepted / 6A-3 local verified；remote CI pending | 分离受控入口与合成 CI、绑定代理 / Web 模式、HTTPS 路由门禁、新卷恢复与不降级数据库的 V1 回退 |

修改已接受决策时新增 ADR 并标记旧记录为 `Superseded`，不要静默重写历史理由。
