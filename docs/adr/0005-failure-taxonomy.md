# ADR-0005：Failure 是显式状态和稳定契约

- 状态：Accepted / Phase-1 tracking paths verified
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
`contract_violation` 和 `loop_budget_exceeded`，并证明 allowlist 与 retry budget 会共同
限制重试。限流、熔断、checkpointer 故障和生产故障注入仍待后续阶段。
