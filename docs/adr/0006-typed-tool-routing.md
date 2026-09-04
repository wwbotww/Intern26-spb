# ADR-0006：使用类型化命令与确定性工具路由

- 状态：Accepted / Phase-4D five-query-tool path verified
- 日期：2026-09-03

## 背景

Query Understanding 可以存在不确定性，但执行边界不能接受模型或客户端生成的任意
工具名和参数字典。若工具选择、参数解析和结果校验全部塞进一个 Node，就无法分别测试
Wrong Tool、契约漂移和 checkpoint 重放。

## 决策

- `Intent` 只能通过服务端 `AgentToolRegistry` 映射到一个默认只读工具；
- `ToolDescriptor` 固定工具名、命令类型、结果 schema、必要槽位和最大尝试次数；
- `WorkflowPolicy` 只从已验证 Slot 构造判别式 Command，不接受自由函数调用；
- `AgentCommandDispatcher` 同时校验请求工具名、Command 类型、返回工具名和返回 Intent；
- `AgentResultValidator` 在展示前校验业务不变量；
- `ToolExecutor` 使用 `(conversation_id, argument_fingerprint)` 查询执行收据，重放时复用
  已有结果；
- Registry 可显式注册 Policy、Device Price、Tracking、Delivery Time 与 Postage；仍未
  注册的能力进入 Handoff，不回落到其他工具。

## 结果

- Prompt Injection 或任意 `tool_name` 无法直接触发执行；
- Query Understanding、Routing、Execution 和 Validation 可以独立测试；
- checkpoint 分支重放不会再次调用已经成功的 Gateway；
- 新增能力需要显式增加 Command、Descriptor、Tool、Validator 和测试，代码量增加但边界
  更清晰。

## Phase-1 证据

- Registry 拒绝重复意图、缺失必需能力和非白名单工具名；
- Policy 对相同会话和参数生成稳定 fingerprint 与 `tool_call_id`；
- LangGraph 从 `execute_tool` 前的历史 checkpoint 重放时命中收据，Fake Gateway 调用次数
  保持为 1；
- 错邮件号和乱序轨迹被 Validator 阻止展示，且不进入重试。

## Phase-3A 证据

- 相同 StateGraph 根据 Intent 分别构造 `TrackingCommand`、`DeliveryTimeCommand` 和
  `PostageCommand`，并到达唯一 Descriptor；
- 可执行路线必须完成地区解析，资费必须包含具体 Decimal 重量；
- 时限/资费 Fake Gateway 的空结果映射为 `no_match`，不与技术失败混用；
- Validator 会阻止错误路线、错误输入重量和无时区查询时间进入响应；
- Tool 层不依赖 Adapter/Workflow 的架构测试已固化，真实 wire mapping 留在 Phase 3B。

## Phase-4D 证据

- `WorkflowPolicy` 可从五类判别式 Slot 构造各自 Command，并到达唯一 Descriptor；
- Policy/Device compatibility Adapter 只接受对应 Command，任意错误 Command 在调用
  V1 Tool 前被拒绝；
- V1 与 V2 共享工具身份、状态和 Evidence 合同，完整结果再经过 Agent Validator；
- Evidence ID 与 Provenance 必须一一对应，金额、观察时间和政策可追溯字段不合法时
  fail-closed；
- 无网络 Demo 的 capability discovery 返回五类可用能力，集成测试验证 Policy 与
  Device 请求分别只到达自己的 Tool。
