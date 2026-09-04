# ADR-0007：V2 Agent API 显式装配并与 V1 并行

- 状态：Accepted / Phase-4D shared-tool boundary verified
- 日期：2026-09-04

## 背景

现有 `/v1/chat` 已被 Web 使用，契约是客户端显式选模式、单轮、单工具、无服务端记忆。
Agent 路径需要自动理解、conversation、interrupt/resume、类型化结果和后续 SSE；直接
改变 V1 会破坏兼容性。与此同时，真实物流 Gateway、凭据和生产持久化尚未就绪，不能
因为 HTTP 路由代码存在就默认发布半成品能力。

## 决策

- 保持 `/v1/chat` schema、执行和健康语义不变；Agent 使用 `/v2/agent/*` 新契约；
- `create_app()` 只有收到显式 `AgentApiDependencies` 或 lifespan dependency factory 时才
  注册 V2，默认 `main.app` 不挂载；
- V2 HTTP Adapter 只依赖窄化 `AgentConversationService` Protocol、Domain Intent 和服务端
  `ToolDescriptor`，不直接构造 LangGraph、Tool、Gateway 或 Fake；
- Graph Output 必须经独立 API DTO 投影，不能返回 Graph State、内部 Node/Event、Prompt
  或未过滤异常；
- Phase 4A 先交付可完整验证的 JSON 子集；Phase 4B 再把公开停止态投影为版本化 SSE，
  不直接转发 LangGraph `astream_events`，建流后的异常以脱敏 `error` 事件终止；
- capability discovery 区分“已声明”与“实际注册”，未配置能力不能误报 available；
- V2 继续复用既有鉴权、限流、Request ID、请求体限制和并发容量中间件。
- V2 使用独立 readiness 语义：持久化/checkpoint 和“至少一个能力”是关键条件，janitor
  降级可继续服务但必须通过状态与指标暴露；未提供探针时 fail-closed；
- Agent 指标只使用有限枚举标签，运行 Trace 只记录哈希会话引用和公开停止态摘要，不把
  prompt、槽位值、结果 data 或 LangGraph 内部状态送入运维面。
- 政策与设备价格通过只依赖 `AssistantTool` Port 的 compatibility Adapter 进入 V2；
  `/v1` 与 `/v2` 共用 Tool 实例和结果合同，Tool 生命周期仍由 V1 Registry 唯一拥有，
  Adapter 不复制检索、引用校验、设备解析或价格匹配逻辑。

## 结果

- V1 回归和当前 Web 不因 Agent 开发发生行为漂移；
- 可使用真实 Stateful Service + Fake Gateway 做 HTTP 集成测试，而不会让 Fake 进入生产
  composition root；
- 真实 Adapter、SSE 和 Web 可分别演进，发布开关由依赖装配而不是路由内部条件决定；
- Phase 4B 已补 lifespan factory、OpenAPI 类型生成和显式 Web 切换；Phase 4C 已验收
  readiness、低基数指标、脱敏 Run Trace 与 janitor 调度；Phase 4D 已验证共享 Tool
  生命周期、完整 Evidence 投影和五能力本地闭环。默认生产入口、完整 node/edge
  Trace 和多副本运行仍需单独验收。
