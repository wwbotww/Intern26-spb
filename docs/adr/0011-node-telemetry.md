# ADR-0011：语义 Trace 与真实节点耗时分层

> 决策记录：下文验收范围保留为记录当时的事实；现状见[当前状态](../current-status.md)。


- 状态：Accepted（local / opt-in V2）
- 日期：2026-09-07

## 背景

checkpoint 审计事件可以解释分支，但无法测量 Node wall-clock；跨请求 interrupt 也不该
保持一个持续数分钟的活跃 span。自动捕获 LangChain input/output 则可能暴露 Prompt、
槽位、工具结果和业务错误正文。

## 决策

在图装配边界包装同步/异步 Node，而非侵入领域 Tool。每次实际 start/resume 创建独立
root，Node 是 children；中断立刻结束 span，重试分别计时，API 幂等重放不执行图。
保留语义 checkpoint Trace，并在采样命中时用 Trace ID 关联，而不把遥测状态持久化。

组合根在 lifespan 内拥有独立 provider/exporter，借用给 Runtime，不注册全局 SDK。
默认禁止导出；采用 ParentBased + TraceIdRatioBased，根上下文不继承外部输入；Node
Prometheus 指标不受采样影响。仅导出固定枚举和有界计数，不使用通用 GenAI input/output
自动埋点。GraphInterrupt 是控制流，不是错误；fail-open 仅适用于遥测，不改变业务的
fail-closed、预算和超时策略。

## 代价与边界

每次 resume 是独立 trace，不提供跨进程/跨服务 parent 传播；跨轮关联仍依赖已有脱敏
语义日志。Head sampling 无法保证收集所有错误，队列满/进程强杀可能丢 span。当前本地
Demo 直连 Tempo，不增加 Collector、tail sampling、W3C baggage 或可靠遥测队列。

生产默认仍为 V1。可复现 Docker Dashboard 是开发证据，不是生产发布或 SLO 验收。
详见 [Phase 5E](../../apps/assistant-api/docs/operations.md)。
