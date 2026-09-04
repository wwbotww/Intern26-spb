# ADR-0005：Failure 是显式状态和稳定契约

- 状态：Accepted / Phase-4A HTTP mapping verified
- 日期：2026-09-03

## 背景

缺少用户输入、查无业务结果、上游超时、响应契约错误、checkpoint 故障和内部异常的
处理方式不同。统一包装成“没有查询到”会误导用户，也无法安全重试或定位故障。

## 决策

所有预期失败先归一为稳定 `FailureCategory`，再由确定性 Policy 路由到 interrupt、
有限重试、完成、Handoff 或 fail-closed 响应。至少区分：

- `missing_input`、`ambiguous_intent`、`invalid_input`；
- `no_match`；
- `upstream_timeout`、`upstream_rate_limited`、`upstream_unavailable`；
- `contract_violation`；
- `state_conflict`、`persistence_unavailable`、`state_schema_incompatible`；
- `loop_budget_exceeded`、`internal_error`。

LangGraph checkpoint 恢复与领域工具重试是两套机制，不能各自无条件重试。只读 Tool
必须使用 argument fingerprint 与执行收据，使节点重放不会再次请求已经成功的上游。

## 结果

- 用户错误、业务空结果和技术故障具有不同语义与指标；
- 只有明确幂等、可恢复的错误允许有限重试；
- HTTP 200 但 schema/业务不变量错误的响应不会进入 UI；
- 阶段 5 必须通过故障注入验证 checkpoint 中断、节点重放、限流、超时和契约漂移。

Phase 1 已在 Fake Tracking 路径验证 `no_match`、`upstream_timeout`、
`contract_violation` 和 `loop_budget_exceeded`。Phase 3A 又用 MockTransport 验证 timeout、
transport、429、5xx、非法 JSON、未知状态与超大响应的稳定映射，并实现 Graph 唯一重试
所有权、有界 jitter、受限 `Retry-After`、按能力熔断和 half-open 恢复。真实 Gateway
fixture、checkpointer I/O 故障、分布式熔断和测试环境故障注入仍待后续阶段。

Phase 4A 把操作级 Failure 映射为稳定的 404/409/422/502/503，并为整个 Graph Run 增加
504 外层 timeout；Workflow 内部已经收口的 `no_match` 或失败仍用类型化 200 响应，避免
把“流程已安全结束”和“本次 HTTP 操作无法推进”混成同一错误。未分类异常继续 fail
closed，不向客户端泄漏内部 message。
