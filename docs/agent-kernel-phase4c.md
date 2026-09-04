# Phase 4C：Agent Operations 与隐私安全可观测性

> 状态：2026-09-04 已完成不依赖真实接口或模型 API Key 的生产化基础切片。
>
> 发布边界：V2 仍只在显式注入 `AgentApiDependencies` 或 factory 时挂载；默认
> `main.app`、Compose 和 V1 行为没有改变。
>
> 后续状态：本文保留 Phase 4C 里程碑事实；V1 政策/价格 Tool 复用和五能力本地闭环
> 已在 [Phase 4D](agent-kernel-phase4d.md) 完成。

## 1. 本阶段完成内容

Phase 4C 补齐了 Stateful Agent 的运行保障闭环：

- 新增 `GET /v2/agent/health/ready`，分别检查 Agent 装配、SQLite 元数据表、LangGraph
  checkpoint 表、janitor 和五类能力；
- V2 readiness 是独立运维端点，不复用 V1 “两个工具必须同时 ready”的语义；
- JSON 与 SSE 共用 Agent run 指标，记录终态、意图、传输、interrupt、失败类别和耗时；
- 增加版本化的脱敏 `agent_run_trace`，只记录公开状态摘要和哈希引用；
- FastAPI lifespan 负责启动、定期运行和关闭 conversation janitor；清理失败会进入
  `degraded`，不会让仍可安全服务的查询整体下线；
- SQLite readiness probe 使用只读 schema 查询，同时确认 Agent metadata/receipt 表与
  LangGraph `checkpoints`/`writes` 表存在。

本阶段刻意没有把 LangGraph `debug`、node input/output、消息历史或 checkpoint 正文写入
日志。当前 Trace 是一次 Graph Run 的公开停止态摘要；更细的 node/edge 时序只能在
Phase 5 通过同样的脱敏投影和采样策略增加。

## 2. Readiness 语义

`GET /v2/agent/health/ready` 不要求业务鉴权和限流，部署时仍应只允许运维网络访问。
响应的固定检查项如下：

| 检查 | 状态 | 是否阻断流量 |
| --- | --- | --- |
| `agent_api` | 显式 V2 依赖已激活 | 是 |
| `persistence` | 会话、创建/消息幂等与 Tool receipt 表完整 | 是 |
| `checkpoint` | LangGraph checkpoint/writes 表完整 | 是 |
| `janitor` | `ready` / `degraded` / `starting` / `disabled` | 否；降级必须告警 |
| `capability.<intent>` | `ready` 或 `disabled` | 只有五类能力全部不可用时阻断 |

关键依赖和至少一个能力可用时返回 HTTP 200。janitor 异常时响应状态为 `degraded`，但
查询仍可服务；持久化、checkpoint 或全部能力不可用时 fail-closed，返回 HTTP 503。
未提供 readiness probe 的自定义装配不会被乐观视为 ready。

## 3. 低基数 Prometheus 指标

新增指标包括：

| 指标 | 有限标签或值 |
| --- | --- |
| `assistant_agent_runs_total` | `transport`、公开 `outcome`、五意图/unknown |
| `assistant_agent_run_duration_seconds` | `transport`、公开 `outcome` |
| `assistant_agent_runs_in_flight` | 无标签 |
| `assistant_agent_interrupts_total` | `collect_slots` / `clarify_intent`、意图 |
| `assistant_agent_failures_total` | `transport`、固定 FailureCategory/内部类别 |
| `assistant_agent_readiness` | 固定组件名 |
| `assistant_agent_janitor_*` | 固定 outcome/resource |

conversation ID、turn ID、request ID、邮件号、城市、failure code 和异常正文都不会作为
Prometheus 标签，避免隐私泄露和时间序列基数失控。HTTP 层原有指标继续独立记录状态码
和模板化 route，因此可区分“HTTP 请求成功”与“工作流进入 waiting_user”。

这里的 run 指标表示一次 V2 message operation；命中持久化幂等收据的重放也计一次运维
观察，但不会再次推进 Graph 或调用 Tool。当前 Service Protocol 没有把 replay flag 暴露
给 API，因此尚不能直接按 `executed/replayed` 拆分指标，不能把该计数解释成唯一 Tool
执行次数。

## 4. 脱敏 Agent Run Trace

每次 JSON/SSE Graph Run 只产生一个 `trace_schema_version=1` 的结构化摘要，内容限于：

- `transport`、`outcome`、`duration_ms`；
- conversation/turn UUID 的 SHA-256 截断引用，不记录原 UUID；
- 公开 phase、intent、next action 和缺失字段名；
- result type/status/reason code，或 failure category/code/retryable；
- warning 数量。

Trace 不包含 prompt、用户原文、槽位值、Tool 参数、结果 data、reply、owner、API Key、
内部 Graph State、checkpoint、node 名或隐藏推理。响应契约异常和未知异常的日志也只记录
异常类型，不序列化可能带业务输入的异常正文。

## 5. Janitor 生命周期

`AgentApiDependencies` 可注入 janitor、周期和单次 timeout。应用启动时先执行一次清理，
随后用一个命名后台任务按周期运行；关闭 lifespan 时通过 stop event 立即结束，不遗留
任务。每次运行都只记录聚合数量：

- 已删除的过期会话数；
- API 幂等收据数；
- Tool 收据数；
- 失败数量。

失败 conversation ID 不进入日志。janitor 与请求共享 `ConversationRunCoordinator`，因此
不会在同一进程内与正在执行的会话并发删除。SQLite 方案仍只承诺本地单进程语义；多副本
部署需要共享 checkpointer、分布式租约/锁和跨实例冲突测试。

## 6. 验收证据

Phase 4C 新增 6 个自动化用例，覆盖：

1. V2 readiness 的免鉴权运维访问、完整检查项、指标和 lifespan 状态；
2. 缺少 persistence probe 时返回 503，而不是误报可用；
3. JSON completed 与 SSE waiting_user 的低基数指标及 Trace 脱敏；
4. failure 指标只使用稳定类别，Trace 不记录上游异常正文或用户原文；
5. janitor 启动即运行、周期调度、聚合清理指标和 partial/degraded 状态；
6. 单次过期清理受配置 batch size 限制。

加入本阶段后，完整 Python workspace 为 `283 passed`；Web 仍为 `15 passed`，类型检查、
production build、OpenAPI 生成类型一致性和 `uv lock --check` 均通过。

验证命令：

```bash
UV_CACHE_DIR=/private/tmp/intern26-uv-cache uv run pytest -q

cd apps/chat-web
npm run check:agent-types
npm test
npm run build
```

## 7. 尚未完成

- 政策/设备价格仍需 V1-to-Agent compatibility adapter；
- 三类物流真实 Adapter、URL、认证、wire schema 和错误码等待接口材料，届时需要 API Key；
- 默认 production composition、Compose 灰度开关和回退演练尚未完成；
- 当前 Trace 不是分布式 tracing，也没有脱敏 node/edge span、采样或外部 collector；
- 代表性 Agent Eval、SLO/告警阈值和多副本持久化仍属于 Phase 5/6。

V1 能力复用 Adapter 随后已由 Phase 4D 完成；真实物流或 Structured Model 接入仍应
等待接口契约、Base URL、认证方式和测试凭据。
