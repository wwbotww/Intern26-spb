# ADR-0001：采用受约束单 Agent

- 状态：Accepted / Phase-1 kernel verified
- 日期：2026-09-03
- 范围：Assistant V2 Agent Workflow

## 背景

目标能力只有政策、设备价格、邮件轨迹、寄递时限和资费五类只读查询。每类工具、
必要参数和结果约束可以预先定义，但用户表达可能含歧义、缺字段或跨轮补充。

## 决策

采用受约束的单 Agent 状态图：LLM 只参与 Structured Query Understanding 和可选的
回答措辞；确定性 Policy 决定澄清、补槽、工具执行、结果校验、恢复或终止。一个正常
业务 Step 最多执行一个白名单工具，并同时限制图 Step、模型调用、工具调用、重试和
deadline。

不采用自由 ReAct、模型生成任意工具名、隐式多工具并行或多 Agent 协商。

## 结果

- 优点：可预测、可审计，能分别评测理解、路由和工具执行；
- 优点：Prompt Injection 不能直接跨过 Tool Registry；
- 代价：新增意图需要显式 schema、Policy 分支和测试；
- 代价：不能处理没有预先建模的开放式任务。

## 验证

- 明确意图的 Wrong Tool Rate 必须为 0；
- 未注册工具和未校验参数必须被拒绝；
- Golden Case 不得耗尽循环预算；
- 新增自治范围必须由后续 ADR 说明风险与评测证据。

Phase 1 已用单 Agent 状态图验证缺槽、单工具执行、结果校验、有限重试、超预算终止和
Handoff 分支；当前证据只覆盖 Fake Tracking Tool，不代表五类能力或生产质量门禁完成。
