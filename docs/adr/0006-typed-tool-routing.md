# ADR-0006：使用类型化命令与确定性工具路由

- 状态：Accepted / Phase-1 kernel verified
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
- Registry 当前只注册 Phase-1 Fake Tracking Tool，未注册能力进入 Handoff，不回落到
  其他工具。

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
