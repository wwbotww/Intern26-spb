# ADR-0003：采用 Hybrid Query Understanding

- 状态：Accepted / Phase-1 rule slice verified, hybrid pending
- 日期：2026-09-03

## 背景

纯规则难覆盖自然语言变体，纯 LLM 又可能误识别邮件号、重量、行政区划等硬字段，且
模型自报 confidence 不能直接当作校准概率。

## 决策

Query Understanding 按以下优先级执行：

```text
显式 UI 意图
  > 当前 thread 工作状态
  > 确定性实体提取与规则
  > Structured LLM fallback
  > Pydantic 校验
  > 确定性 Workflow Policy
```

输出固定为版本化 `QueryUnderstandingResult`，包含候选意图、公开 signals、槽位来源、
缺失字段和歧义。模型不能输出可执行函数、任意工具名或绕过 schema 的参数。

阶段 0 Spike 中的 13 位数字提取只验证 Graph 分支，不是最终邮件号规则；真实校验等待
轨迹接口契约。

## 结果

- 规则可解决的输入不产生模型成本；
- 硬字段和低置信输入更容易解释、回归与澄清；
- Pipeline 需要独立评测规则命中率、fallback 率、Intent Macro-F1、Slot F1 和不必要
  澄清率；
- Prompt、Parser、规则和数据集必须分别版本化。

Phase 1 已实现可离线测试的轨迹规则切片，支持当前 Workflow 上下文、显式意图、13 位
国内邮件号和国际邮件号样式。它不包含五意图完整规则、Region Resolver、Slot Merger 或
Structured LLM fallback，因此 Hybrid Pipeline 仍属于阶段 2。
