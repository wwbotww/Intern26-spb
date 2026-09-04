# ADR-0008：Agent 质量门禁使用公开多轮黑盒行为

- 状态：Accepted / Phase-5A local baseline verified
- 日期：2026-09-04

## 背景

Agent 单元测试可以证明 Parser、Policy、Node 和 Tool 的局部不变量，却不能证明调用方
经过 HTTP、conversation、LangGraph interrupt/resume 和结果投影后实际获得的行为。
如果 Eval 直接导入 Workflow、读取 checkpoint 或调用 Gateway，它会绕过最容易发生
集成错误的边界，并与服务实现形成反向依赖。

同时，“准确率”如果没有冻结数据、样本量、阈值和失败明细，很容易成为无法复现的
展示数字。质量门禁必须让 API 错误、错误路由和未完成的多轮场景进入分母，不能只统计
成功请求。

## 决策

- Agent Eval 只调用公开 `/v2/agent/health/ready`、`capabilities` 和 `messages`；不得导入
  `spb_assistant_api`、读取 SQLite/checkpoint 或直连 Tool 数据源；
- 数据集以场景为并发单位、以 Turn 为顺序单位；同一场景复用服务端返回的
  `conversation_id`，真实验证 interrupt/resume；
- 每个请求使用不包含问题正文的随机运行前缀、样本 ID 哈希和 Turn 序号构造幂等键；
- 评测端对 V2 JSON 响应做独立 Pydantic schema gate；HTTP 错误和契约错误保存为脱敏
  observation，停止该场景后续 Turn，但不终止整批评测；
- 首批指标固定为 Intent Accuracy、Required Input Accuracy、Wrong Tool Rate、Task
  Completion Rate、Recovery Rate、API Error Rate 和 Turn P50/P95；
- API 错误、缺失 Turn 和错误状态都导致场景失败。Wrong Tool 以公开 `result.type` 与
  Gold intent 比较，不依赖内部 Tool 名；
- 门禁阈值写入 `AgentRunConfig` 和报告，默认要求 Golden 场景通过率 = 1、Intent/
  Required Input ≥ 0.95、Wrong Tool/API Error ≤ 0、Task Completion/Recovery ≥ 0.90；
- 报告必须包含 dataset SHA256、Git 工作树状态、服务版本、能力快照、分母和 review
  queue；`--fail-on-gate` 在报告落盘后以退出码 3 阻断 CI；
- 公开 fixture baseline 与真实生产质量报告严格分开，不能把小型确定性样本的 100%
  表述为生产准确率或 Macro-F1。

## 结果

- Eval 与 Agent 实现保持单向 HTTP 依赖，能发现 API 投影、conversation 关联和持久化
  恢复问题；
- 失败运行仍能产出逐场景证据，不会因一个 503 丢失整批诊断信息；
- 指标均带明确分母，质量阈值与实际值可以进入 CI 和面试复盘；
- Phase 5A 的本地五能力基线覆盖 13 个场景、17 个 Turn、4 个多轮恢复场景；该结果只
  是确定性 fixture 回归证据；
- 故障注入、baseline/experiment 对比、完整 node/edge Trace 与真实接口 holdout 报告
  留给 Phase 5B 及真实 Adapter 阶段。
