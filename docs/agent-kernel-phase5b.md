# Phase 5B：可靠性故障矩阵、语义 Trace 与 Agent 报告对比

> 状态：`Implemented / local reliability verified`
> 日期：2026-09-04
> 版本：`assistant-api 0.3.3`、`eval 0.5.0`
> 范围：本地 LangGraph + Fake Gateway + SQLite/InMemory checkpoint；不代表真实接口 SLO。

## 1. 阶段目标

Phase 5A 回答了“公开 V2 多轮行为是否符合 Gold”。Phase 5B 继续回答三个工程问题：

1. 一次 Workflow 为什么澄清、重试、失败或完成，能否在不泄露业务数据的前提下定位；
2. timeout、契约漂移、Loop Budget、interrupt/resume 和 checkpoint 重放是否有确定语义；
3. baseline 与 experiment 是否能在完全相同的样本条件下识别总体及逐 Turn 回归。

本阶段不需要模型或物流接口 API Key。真实 Gateway 与 Structured Model holdout 继续等待
接口合同和凭据。

## 2. 实现内容

### 2.1 LangGraph 语义时间线

`workflow/tracing.py` 从 Graph Run 前后的 checkpoint 中提取本次新增 `AgentEvent`，生成
`AgentWorkflowTrace`。它不是原始 LangGraph debug event，也不进入公开 JSON/SSE 协议。

一次 Trace 可以区分：

- `node_path`：本次经过的语义 Node；
- `edge_path`：相邻 Node 及 `__interrupt__` / `__end__` / `__error__` 终点；
- `resumed` / `interrupted`：是否从 HITL 中断恢复、是否再次等待用户；
- `checkpoint_before` / `checkpoint_after`：运行前后是否存在持久化状态；
- `loop_step_count`、`logical_tool_call_count`、`retry_count`；
- Tool start/success/failure/reuse、失败分类、恢复调度和结果校验事件。

Runtime 通过可注入 `WorkflowTraceSink` 输出 Trace。默认装配写入独立
`spb_assistant_api.workflow_trace` Logger；Sink 或 Trace 所需的只读 checkpoint 查询失败
不会改变主流程结果。

示例字段如下，引用值仅为格式示意：

```json
{
  "trace_schema_version": "1",
  "trace_type": "agent_workflow",
  "conversation_ref": "sha256:…",
  "turn_ref": "sha256:…",
  "outcome": "completed",
  "resumed": false,
  "interrupted": false,
  "checkpoint_before": false,
  "checkpoint_after": true,
  "node_path": [
    "ingest",
    "understand",
    "decide_next",
    "execute_tool",
    "validate_result",
    "recover",
    "decide_next",
    "execute_tool",
    "validate_result",
    "compose_response"
  ],
  "retry_count": 1,
  "logical_tool_call_count": 1
}
```

### 2.2 隐私与稳定性边界

Trace 只允许固定字段：Node/Phase、Intent、Action、Parser/Prompt 版本、Tool 名、Attempt、
Failure Category、Retry、结果状态及计数。以下内容不会进入结构化日志：

- 用户问题原文与补槽值；
- 邮件号、地区、重量及工具参数；
- 工具结果 `data`、回复正文和上游错误正文；
- 完整 Graph State、checkpoint payload、Prompt 和 Chain of Thought；
- 原始 conversation/turn ID。

事件最多保留 64 条；未知 detail 被丢弃，非固定格式 code 归一为 `unclassified`。若运行前
checkpoint 无法读取，只记录有界尾部并把 `event_window` 标记为 `tail_fallback`。

### 2.3 Agent baseline / experiment 对比

Eval 新增 `agent-compare`：

```bash
uv run --package spb-eval spb-eval agent-compare \
  --baseline eval/reports/<baseline>/run.json \
  --experiment eval/reports/<experiment>/run.json
```

命令在计算差值前强制验证：

- 两份报告都有且共享同一 `dataset_sha256`；
- 场景 ID 与完整 Gold Turn 标签一致；
- Eval 质量门禁阈值一致；
- 报告和场景内不存在重复 ID / Turn index。

通过校验后，比较器从逐 Turn observation 重新计算两侧 summary，不直接信任可能陈旧的
保存汇总。

输出 `agent-comparison.json` 与 `agent-comparison.md`，覆盖场景/Turn 通过率、Intent、
Required Input、非必要澄清、Wrong Tool、Task Completion、Recovery、API Error 和 Turn
P95。每项指标声明“越高越好”或“越低越好”，并列出逐 Turn improvement/regression；
缺失 Turn 与 API error 不会被忽略。

## 3. 本地故障注入矩阵

下表复用类型化 Fake Gateway、MockTransport、InMemory/SQLite Checkpointer 和持久化 Tool
Receipt。它验证错误语义与恢复边界，不模拟真实网络容量。

| 场景 | 注入点 | 期望行为 | 可复核测试 |
| --- | --- | --- | --- |
| 首次 timeout、第二次成功 | Tracking Gateway | 同一逻辑调用有限重试并完成，Trace 显示两次 attempt | `test_workflow_trace_explains_retry_without_logging_business_values` |
| 连续 timeout | Tracking Gateway | 重试一次后稳定 `upstream_timeout` 失败 | `test_fault_outcomes_are_visible_in_the_sanitized_trace` |
| 结果邮件号漂移 | Result Validator | `contract_violation`、不展示错误结果、不重试 | 同上 |
| Step Budget 耗尽 | Workflow Policy | `loop_budget_exceeded`，不继续调用 Tool | 同上 |
| 缺槽 interrupt/resume | LangGraph checkpoint | 两次 Trace 分别标记 interrupt 和 resume，仅第二次包含增量事件 | `test_workflow_trace_distinguishes_interrupt_resume_and_checkpoint` |
| execute 前 checkpoint 重放 | Tool Receipt | 复用 receipt，Gateway 总调用仍为一次 | `test_checkpoint_replay_reuses_receipt_without_second_gateway_call` |
| 429 / 5xx / 504 / 非法 JSON | HTTP Adapter | 映射 rate limited、unavailable、timeout、contract violation | `test_http_failures_map_to_stable_agent_taxonomy` |
| 同 conversation 并发推进 | Run Coordinator | 第二个 Run 以 `state_conflict` fail-fast，Tool 不重复调用 | `test_concurrent_resume_is_rejected_before_second_tool_call` |
| 外层请求 timeout 后重放 | V2 API + idempotency | 释放处理中收据，同 Key 安全重试并完成 | `test_outer_timeout_releases_message_claim_for_safe_retry` |
| SQLite 重启后 resume | Checkpointer + Metadata | 同 thread 恢复 interrupt，且不重复 Tool | `test_interrupted_graph_resumes_after_sqlite_restart_without_duplicate_tool` |
| Graph recursion limit | LangGraph Runtime | 映射稳定 `loop_budget_exceeded` | `test_spike_uses_explicit_recursion_limit_as_last_resort` |

## 4. 模块边界

| 模块 | 本阶段职责 | 不承担的职责 |
| --- | --- | --- |
| `domain/agent_events.py` | 定义可序列化语义事件 | 日志格式、框架 callback |
| `workflow/tracing.py` | 从运行前后状态构造脱敏 Trace DTO | 写日志、公开 API 投影 |
| `workflow/runtime.py` | 围绕 `ainvoke` 采集 checkpoint 增量并调用 Sink | 吞掉业务异常、暴露完整 State |
| `observability/agent_trace.py` | 哈希 ID 并输出固定结构化日志 | 参与 Agent 决策 |
| `eval/analysis.py` | 验证可比性并计算指标/Turn 差异 | 导入 Assistant 或读取 checkpoint |
| `eval/reporting.py` | 生成 JSON/Markdown 对比证据 | 修改评测结论 |

公开 V2 SSE 仍是稳定业务投影，内部 Trace 不会转发给浏览器；Eval 仍只通过 HTTP 运行
Agent 数据集，报告对比只读取已保存报告。

## 5. 验证证据

本阶段新增 12 个测试 case：

- Assistant Trace 与故障可解释性：7 个；
- Eval Agent 报告可比性、差异、落盘和 CLI：5 个。

阶段用例验证了 Trace 不包含邮件号、问题文字、结果字段和上游错误正文；Assistant 与
Eval 联合测试通过。完整 Python 工作区 `308 passed`；本阶段未修改 Web，类型生成检查、
`17 passed` 和 production build 均通过；`uv lock --check` 通过。

## 6. 当前限制与下一步

- Trace 是语义事件时间线，不是逐 Node wall-clock span；尚未接 OpenTelemetry exporter；
- checkpoint 前后只读查询在启用 Trace 时增加固定 I/O，生产需配置采样；
- 故障矩阵使用本地 Fake/Mock，不可宣称真实接口恢复率或生产 SLO；
- Agent compare 不用 LLM-as-a-Judge，语义答案质量仍依赖 Gold 与人工 review queue；
- 真实轨迹/时限/资费接口到达后，应进入 Phase 3B Adapter 接入并运行独立故障 holdout；
- Structured Model API Key 到达后，应对规则 baseline 与模型 experiment 运行同样本
  `agent-compare`，回填 Macro-F1、Slot F1、fallback 和 Wrong Tool 指标。

核心决策见
[ADR-0009](adr/0009-semantic-workflow-trace-and-agent-comparison.md)。
