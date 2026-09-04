# ADR-0009：使用脱敏语义 Trace 与同样本 Agent 对比定位回归

- 状态：Accepted / Phase-5B local reliability verified
- 日期：2026-09-04

## 背景

Phase 4C 的 `agent_run_trace` 只记录一次请求的终态摘要。它适合低基数运行指标，却无法
回答“经过了哪些 Node、哪条条件边、是否发生 interrupt/resume、为什么重试”等定位问题。
LangGraph 的 `astream_events` 虽然更完整，但直接写日志或返回浏览器会携带内部 State、
Prompt、工具参数和框架事件结构，既扩大敏感信息面，也会把框架实现变成公开契约。

另一方面，Phase 5A 已能生成 Agent 质量报告，但没有受约束的 baseline/experiment
比较。如果两次运行的样本、Gold 或门禁阈值不同，指标差值没有可比性，甚至可能制造
“样本变简单后效果提升”的错误结论。

## 决策

- Node 继续只产生显式 `AgentEvent`，不记录 Chain of Thought；Runtime 在调用前后读取
  checkpoint，仅投影本次新增的审计事件；
- Trace 使用固定白名单字段。允许 Node、Phase、Action、Intent、Parser/Prompt 版本、
  Tool 名、Attempt、Failure Category、Retry 和结果状态；禁止用户原文、槽位值、工具
  参数、结果 `data`、完整 State 与 checkpoint 内容；
- conversation/turn 只在内存 Trace DTO 中用于关联，结构化日志始终写 SHA256 截断引用，
  不把原始 ID 当作日志字段或指标标签；
- 每次 Trace 显式记录 `node_path`、推导的 `edge_path`、interrupt/resume、checkpoint
  前后存在性、逻辑 Tool Call、Retry 与 Loop Step。事件上限为 64，未知字段丢弃，非法
  code 归一为 `unclassified`；
- interrupt 在 `clarify` Node 内抛出，Node 无法先提交返回值，因此该 Node 由终态和
  checkpoint `next` 安全推导，并在实现中明确标注；
- Trace 为 best effort：前后 checkpoint 读取或日志 Sink 失败不能改变 Workflow 结果；
  无法读取前置 checkpoint 时只输出有界尾部并标记 `tail_fallback`，不得假装是精确增量；
- Agent 报告比较必须同时满足 dataset SHA256 相同、场景 ID 相同、完整 Gold 标签相同、
  质量门禁阈值相同；否则拒绝生成报告；
- 比较输出固定核心指标方向，并列出逐 Turn 的 regression/improvement 及失败检查状态；
  API error、缺失 Turn 和契约断言失败都参与比较；指标从逐 Turn observation 重算，
  不直接信任保存报告中可能陈旧的 summary。

## 结果

- 运行日志可以沿着 Node、条件边、Tool retry 和 interrupt/resume 解释决策路径，又不暴露
  Prompt、业务参数或模型思维链；
- Trace 与公开 SSE 解耦，重构内部图不会破坏 Web 协议；
- Agent 实验只能在冻结样本上比较，报告既展示总体指标，也保留定位到场景/Turn 的回归
  证据；
- best-effort checkpoint 读取会给开启 Trace 的本地运行增加两次只读操作；生产阶段需要
  结合采样和 OpenTelemetry exporter 评估成本；
- 当前 Trace 是语义时间线，不提供每个 Node 的独立耗时 span；真实接口的分布式 Trace、
  采样策略和代表性 holdout 仍属于后续工作。
