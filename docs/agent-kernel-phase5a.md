# Phase 5A：Agent 多轮黑盒评测与质量门禁

> 状态：2026-09-04 已完成本地确定性基线；不依赖外部 API Key。
>
> 证据边界：本阶段数字来自五能力 Demo、Fake Gateway、本地政策/价格 fixture 和 SQLite，
> 只证明评测链路与回归样本，不代表真实接口、Structured Model 或生产流量质量。

## 1. 本阶段目标

Phase 5A 把已有 Eval 从单轮 RAG/V1 Assistant 扩展到真正的 Stateful Agent 黑盒评测：

- 只通过 `/v2/agent/*` 观察系统，不导入 Assistant 实现或读取 checkpoint；
- 场景之间可并发，场景内部按顺序复用 `conversation_id`；
- 覆盖五意图、补槽、多意图选择、业务无结果、工具信息不足、未知 Handoff 和控制命令；
- 生成可解释指标、失败样本、人工复核队列和机器可读质量门禁；
- 明确小型 fixture baseline 与代表性生产评测的表述边界。

## 2. 评测链路

```text
versioned JSONL scenarios
          |
          v
AgentApiClient --HTTP--> /v2/agent/health/ready
          |              /v2/agent/capabilities
          |              /v2/agent/messages
          v
strict response schema gate
          |
          v
turn checks -> case checks -> metrics -> quality gate
                                      -> review queue
```

Eval 不依赖 `spb_assistant_api`。架构测试会扫描 `eval/src/spb_eval` 的 Python import，
防止评测端为了方便绕过 HTTP 边界。

## 3. 数据集与执行语义

公开数据集为 `eval/datasets/agent-workflow-v1.jsonl`，当前包含 13 个场景、17 个 Turn：

| 场景类型 | 数量 | 重点 |
| --- | ---: | --- |
| 五意图单轮成功 | 5 | 意图、结果类型、状态与关键字段 |
| 多轮补槽 | 3 | `waiting_user -> interrupt/resume -> completed` |
| 多意图澄清 | 1 | `clarify_intent` 后显式选择并恢复 |
| 业务无匹配 | 1 | `no_match` 与技术失败分离 |
| Tool 信息不足 | 1 | V1 `need_more_info` 投影保持稳定 |
| 未知 Handoff | 1 | 不猜测意图、不调用工具 |
| 控制命令 | 1 | 取消后安全完成并清空意图 |

数据划分为 7 个 calibration 场景 / 7 Turn 与 6 个 holdout 场景 / 10 Turn；报告同时
保存按 split 的样本量与通过率。

Runner 以场景为并发单位，同一场景的 Turn 严格串行。每轮幂等键由随机运行 token、
样本 ID SHA256 短摘要和 Turn 序号构成，不包含问题、邮件号或槽位值。某轮发生 API/
契约错误时保留 observation 并停止该场景后续 Turn，其他场景继续执行。

原有 `agent-understanding-v1.jsonl` 仍用于内部 Understanding schema 回归；黑盒数据集只
断言公开 API 可观察字段，避免把内部 Graph State 当作外部契约。

## 4. 指标口径

| 指标 | 口径 |
| --- | --- |
| Intent Accuracy | 成功返回的 Turn 中，公开 intent 与 Gold 一致的比例；Handoff/控制使用 `null` |
| Required Input Accuracy | 预期等待用户的 Turn 中，required input 名称及顺序完全一致的比例 |
| Wrong Tool Rate | 产生 Result 的 Turn 中，`result.type` 与 Gold intent 不一致的比例 |
| Task Completion Rate | 预期获得 success/partial/no_match 的任务场景完整通过比例 |
| Recovery Rate | 所有多轮场景中，每轮状态、恢复和最终断言全部通过的比例 |
| API Error Rate | 已执行 Turn 中 HTTP、网络或评测端契约错误的比例 |
| Turn P50/P95 | 评测客户端观察到的单轮 HTTP 延迟；不是生产 SLA |

场景通过要求已观察 Turn 数与数据集一致，且每轮 phase、intent、next action、required
inputs、result/failure 和声明的结果路径全部通过。API 错误不会从质量分母中被过滤。

## 5. 质量门禁

默认阈值如下，且会连同实际值写入 `run.json` 与 `quality-gate.json`：

| 门禁 | 默认阈值 |
| --- | ---: |
| Golden 场景通过率 | = 1.00 |
| Intent Accuracy | ≥ 0.95 |
| Required Input Accuracy | ≥ 0.95 |
| Wrong Tool Rate | ≤ 0.00 |
| Task Completion Rate | ≥ 0.90 |
| Recovery Rate | ≥ 0.90 |
| API Error Rate | ≤ 0.00 |

`--fail-on-gate` 会先完整保存报告，再以退出码 3 表示门禁失败；数据集错误或参数错误仍
使用退出码 2。这样 CI 可以保留失败证据，而不是在首个错误处终止。

## 6. 本地基线

2026-09-04 使用服务版本 `0.3.2`、数据集 SHA256
`12a42ff261085a4f00dba308525b9cc0720c377ac02f4ec3ddb8c03cf6b12298`、4 场景并发运行：

| 指标 | 结果 | 分母 |
| --- | ---: | ---: |
| 场景通过率 | 1.0000 | 13 |
| Turn 通过率 | 1.0000 | 17 |
| Intent Accuracy | 1.0000 | 17 |
| Required Input Accuracy | 1.0000 | 4 |
| Wrong Tool Rate | 0.0000 | 11 |
| Task Completion Rate | 1.0000 | 10 |
| Recovery Rate | 1.0000 | 4 |
| API Error Rate | 0.0000 | 17 |
| Turn P50 / P95 | 47.589 / 66.972 ms | 17 |

七项质量门禁全部通过，review queue 为空。首跑曾把未知 Handoff 的公开 intent 错标为
`unknown`，报告准确发现 API 实际返回 `null`；根据公开契约修正 Gold 后重跑通过。这是
评测驱动契约校准的实例，不是通过放宽阈值隐藏失败。

可提交的精简证据位于 `eval/baselines/phase5a-local-fixture-v1/`；包含 dataset hash、
阈值、分母和指标，不包含 API Key。完整逐 Turn 临时报告继续写入被 Git 忽略的
`eval/reports/`。

## 7. 使用方式

终端一启动本地五能力 Demo：

```bash
ASSISTANT_AGENT_DEMO_DB=/tmp/spb-agent-eval.db \
uv run --package spb-assistant-api spb-assistant-agent-demo
```

终端二运行门禁：

```bash
uv run --package spb-eval spb-eval agent-run \
  --dataset eval/datasets/agent-workflow-v1.jsonl \
  --label phase5a-local-fixture \
  --base-url http://127.0.0.1:8081 \
  --concurrency 4 \
  --fail-on-gate
```

真实环境使用 `EVAL_AGENT_BASE_URL` 与 `EVAL_AGENT_API_KEY`。密钥仅用于 Authorization
请求头，不进入报告。

## 8. 测试与边界

Phase 5A 新增 5 个测试，覆盖公开数据集、HTTP 多轮上下文、场景级并发、独立响应
schema、密钥不落报告、指标门禁、失败 review queue、CLI 参数和 Eval 架构边界。
当前 Eval 包 `29 passed`，完整 Python workspace `296 passed`；本阶段未修改 Web，沿用
Phase 4D 的 `17 passed`、类型检查和 production build 证据。

尚未完成：

- 当前 13 场景是工程 fixture，尚不能宣称代表性 Macro-F1 或生产准确率；
- Tool 返回 `need_more_info` 当前是 completed 业务结果，不是第二次 LangGraph interrupt；
- 未加入 timeout、429、5xx、非法 JSON、checkpoint 重放和 Loop Budget 的系统化故障集；
- 未实现 baseline/experiment 的 Agent 专项差异报告；
- 现有 Trace 是脱敏停止态摘要，尚未覆盖完整 node/edge/checkpoint 时间线；
- 真实物流 Gateway 和 Structured Model 仍等待接口合同与 API Key。

下一步 Phase 5B 应建立故障注入矩阵、Agent 报告对比、checkpoint/interrupt 重放评测和
细粒度 LangGraph Trace；这些仍可先使用本地 Fault Gateway，不需要外部密钥。

上述本地可靠性内容随后已由
[Phase 5B](agent-kernel-phase5b.md) 完成；真实物流 Gateway、Structured Model holdout、
逐 Node wall-clock span 和生产采样仍未完成。
