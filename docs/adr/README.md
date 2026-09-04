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

修改已接受决策时新增 ADR 并标记旧记录为 `Superseded`，不要静默重写历史理由。
