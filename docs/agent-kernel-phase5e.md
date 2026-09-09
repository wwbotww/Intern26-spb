# Phase 5E：逐节点 OpenTelemetry 与本地可观测性闭环

> 状态：`Implemented / local synthetic stack verified`；日期：2026-09-07。
> 版本：`assistant-api 0.3.6`，OpenTelemetry SDK / OTLP HTTP `1.44.0`。
> 前置提交：`559161f`（模型接入、Phase 5C/5D）；本阶段没有付费模型调用。
> 当前完整 Python `443 passed`，其中本阶段新增 32 个测试 case。

## 1. 本阶段解决的问题

Phase 5B 的 checkpoint 增量事件能解释“为什么走这条路径”，但没有真实节点起止时间；
它推导的 node/edge 路径不能冒充 OTel span。现在保留语义事件，另在实际执行边界计时。

| 观测层 | 回答的问题 | 计数与耗时边界 |
| --- | --- | --- |
| API Run 指标 | 用户一次 JSON/SSE 操作的结果、延迟 | 包含 API 幂等重放；不包含客户端渲染 |
| `agent.workflow` root span | 一次实际 Graph start/resume 花了多久 | 包含调用内的 checkpoint / 调度；不是完整跨轮会话 |
| `agent.node.<name>` child span | 时间花在哪个节点 | 实际函数体 + await，含工具 I/O、recover 退避；不含节点外 checkpoint 写入或人类等待 |
| checkpoint 语义 Trace | 为何澄清、重试、复用或 handoff | 继续记录增量事件；采样命中时增加 Trace ID 关联 |

本阶段不增加新的公开 API 字段、不改变 Node 的业务决策，也不改写历史评测 baseline。

## 2. 模块边界与生命周期

- `workflow/instrumentation.py` 在图装配时包装 8 个正式节点，保留同步/异步形态，仍由
  LangGraph 负责执行器和上下文传播。只有此处识别 `GraphInterrupt`，领域 Tool 不依赖 SDK。
- `StatefulAgentRuntime` 包装实际 `_invoke`；API 收据直接返回时不进入 Runtime，因而不会
  因重放虚增 Node 或 Workflow span。
- `observability/telemetry.py` 接收有限字段，不接收 Graph State。每次 invocation 是独立
  root；子节点沿上下文成为 children。并发请求不能共用父 span。
- `agent_demo` 的 lifespan 唯一拥有 SDK provider、batch processor 和 HTTP session，向
  Runtime 借用 tracer，向 API 和节点共享同一 `ServiceMetrics`；不注册进程全局 provider。
- 默认 `main.app` 仍仅 V1；仅设置 OTel 开关不会自动发布 V2。自行装配 Runtime 的调用方
  要显式注入 telemetry，SDK 生命周期仍属于其组合根。

`interrupt` 当场结束 clarify span，outcome=`interrupted`，不标记 ERROR。用户下一次
请求创建新的 root，resume 的 clarify 再执行一遍，outcome=`success`。不把 Trace ID 写入
checkpoint，不持久化 span，也不跨人类等待保留“长时间运行”的 span。

重试每执行一次 `execute_tool` 都有独立 span，`agent.tool.attempt` 区分尝试次数；工具
收据命中标记 `agent.tool.reused`。预期类型化失败为 `failed`，未处理异常为 `error`，
两者标记 ERROR；取消继续向调用方传播，标记 `cancelled`。handoff 本身是业务终态，
不自动等于基础设施故障。

## 3. 隐私、采样与失效隔离

仅导出固定 service/version、Node 名、start/resume、结果枚举、允许的 FailureCategory、
有界 tool attempt 和 reused。没有用户输入、原始槽位、工具参数/结果、Prompt、Key、
原始 conversation/turn/request ID、异常 message/stacktrace 或自动事件捕获。
现有语义日志保留其哈希会话引用，采样命中时增加 `trace_id/span_id`，但不作为指标标签。

采用 `ParentBased(TraceIdRatioBased(ratio))`，root 显式创建空上下文，故不会继承客户端
伪造的 traceparent、采样位或 baggage。默认比例 0.1；本地 demo 为 1。ratio=0 仍完整统计
Prometheus 指标，只关闭 span 采集。Head sampling **不保证保留每一条错误 Trace**；本阶段
没有 Collector tail sampling、跨服务 W3C 传播、Metrics exemplar 或 checkpoint 专属 span。

导出默认关闭；开启才实例化 OTLP HTTP exporter。队列最多 256 spans，每批最多 64，
定时 1 秒，默认单批 exporter timeout 2 秒。网络发生在 SDK worker；exporter 可按自身
策略在 timeout 预算内重试，不是工具或模型重试。SDK 队列满会丢遥测，本阶段不提供可靠
交付保证或队列丢弃计数。`export_timeout_millis` 不代替 HTTP exporter 的真实 timeout。

导出端点必须是无凭据/查询/片段的 HTTP(S) `/v1/traces` URL；HTTP 仅用于受信任本地
网络，跨主机需另行做 TLS / Collector 鉴权设计。HTTP session 不读取代理/netrc，禁止
redirect，不消费接收端正文，错误文本替换为固定代码；显式 headers/resource 避免环境
`OTEL_*` 意外附带数据。未安装自动 HTTP/LangChain instrumentation。

初始化、记录、指标、导出及关闭失败不会覆盖业务结果，也不记录原异常正文；尽可能
增加 `assistant_agent_telemetry_errors_total{operation}`。关闭时在工作线程执行 SDK
shutdown 并排空有界队列，不阻塞事件循环；这不是进程硬 kill 时零丢失或无限导出重试。

## 4. 指标与 Dashboard

新增指标：

| 指标 | 标签 | 口径 |
| --- | --- | --- |
| `assistant_agent_node_executions_total` | node, outcome | 实际 Node 次数，重试/恢复各计一次 |
| `assistant_agent_node_duration_seconds` | node, outcome | 秒制 Histogram，包含 await，排除人类等待 |
| `assistant_agent_workflow_invocations_total` | mode, outcome | 实际 Graph 次数，不含 API 幂等重放 |
| `assistant_agent_telemetry_errors_total` | operation | 遥测链路自身故障，不计入业务失败 |
| `assistant_agent_trace_export_batches_total` | outcome | OTLP 批次成功/失败，不是请求数或节点数 |

所有标签都有固定集合，未知值归一为 unknown。Dashboard 共 10 个面板：边界说明、
Workflow 累计次数、API P95、Node rate、Node P95、Node failure fraction、遥测错误、
导出批次、Prometheus target up、可下钻的 Workflow Trace 表格；另有 `agent-trace`
只读详情 Dashboard（说明 + Traces panel）。Trace ID 链接进入该详情页，不要求提升
匿名 Viewer 为 Editor，也不依赖 Viewer 无权访问的 Explore。点击 Trace ID 后选择
`View node spans`（Grafana 自带的 Explore 链接仍可能同时出现在菜单中）。

失败率分子为 failed/error，分母为全部节点执行；无失败但有流量应为 0，无流量或没有
足够 scrape 不强行补成 0。Histogram P95 是分桶估算，小样本不能作为 SLA；精确单次
耗时应看 Trace。指标全量，Trace 采样，两者不能直接用数量相除评估业务质量。

## 5. 可复现本地演示

独立配置位于 [deploy/observability](../deploy/observability/docker-compose.yml)，不修改
默认生产 Compose 拓扑，不连接 RAG、MySQL 或 DeepSeek，也不加载任何 `.env`。

```bash
docker compose -f deploy/observability/docker-compose.yml up -d --build
# 等待 /v2/agent/health/ready 与 Tempo /ready 就绪后：
uv run --package spb-assistant-api python deploy/observability/smoke.py
```

| 入口 | 地址 |
| --- | --- |
| 合成 Agent Demo | `http://127.0.0.1:18081` |
| Grafana Dashboard | `http://127.0.0.1:13001/d/agent-workflow` |
| Prometheus | `http://127.0.0.1:19091` |
| Tempo readiness / API | `http://127.0.0.1:13200/ready` |
| 本机 OTLP HTTP | `http://127.0.0.1:14318/v1/traces` |

Grafana 固定 `12.4.8`、Tempo `2.10.3`、Prometheus `3.5.0`；这是一组验证版本，不宣称
最新版本。所有端口仅绑定回环地址，Grafana 匿名 Viewer、禁用初始 admin 创建；没有
密码或云服务账号。本地用途，不应转发公网。数据全在有容量限制的 tmpfs，容器停止
就会失去演示会话/Trace/指标；需要保留证据时先记录烟测摘要。停止命令：

```bash
docker compose -f deploy/observability/docker-compose.yml down
```

本机若出现 `docker-credential-desktop` 路径缺失，需要修复本机 Docker 安装或使用临时
无凭据配置匿名拉取公共镜像；不要为本项目覆盖全局 Docker 配置。

## 6. 验证证据与限制

- 新增 32 个自动化 case：真实 Graph 父子 span、interrupt/resume、retry、JSON/SSE
  重放、并发上下文隔离、取消/异常传播、ratio=0、配置与资源隐私、Exporter 降级和配置合同。
- 全 Python 工作区 `443 passed`；124 个依赖的锁文件离线检查通过。未修改 Web，沿用
  2026-09-06 Web 验证证据，不把 Grafana 检查当作 chat-web 回归。
- 隔离 Docker 构建、健康检查、真实 OTLP HTTP → Tempo、Prometheus scrape 和 Grafana
  provisioning 已验证。最终合成烟测为 5 次 invocation、2 次幂等重放、5 条新增 Workflow
  Trace、24 个 Node spans、10 个面板；所有 PromQL 均经真实 Prometheus 查询校验。
- 烟测涵盖单轮轨迹、三步资费补槽与 unknown→handoff；首次烟测误将 unknown 期望为
  waiting_user，按现有业务设计修正断言后重跑，没有为烟测改变路由行为。
- 浏览器验证曲线和 Trace 表格；这些合成延迟、UI 和传输证据不是模型质量、真实接口
  故障恢复率或生产 SLO。真实模型请求为 0，旧的 20 次预算没有复用。
- 浏览器最终验证 `View node spans` 可在匿名 Viewer 下显示真实父子时间线；先前默认
  Explore 入口因权限跳回首页，已增加只读详情并保留最小权限。主面板说明区和无失败
  有流量时的 0 失败率显示也经过修正。Demo 镜像使用本机已缓存的 amd64 基础镜像，
  在 ARM Docker Desktop 上运行；不将该演示耗时作为原生架构性能证据。

## 7. 后续顺序

1. 人工审核独立 holdout，固定代码/Prompt/配置后另获真实模型预算；不可自动签字。
2. 物流合同到达后做阶段 3B 真实 Adapter 与测试环境故障报告。
3. 等待外部资料时可做阶段 6A 的 V2 生产装配准备：单实例、默认关闭、SQLite 卷、
   鉴权/owner、Web 开关、CI 与 V1 回退；缺合同能力必须 unavailable，不自动上线 Fake。
4. 真实集成通过后再完成发布、运维与多副本持久化协调；本阶段不改变生产默认入口。

实现决策见 [ADR-0011](adr/0011-node-telemetry.md)，面试表述见
[复盘文档](project-retrospective.md)。SDK 用法依据
[OpenTelemetry Python](https://opentelemetry.io/docs/languages/python/instrumentation/)、
[批量 Span 导出 API](https://opentelemetry-python.readthedocs.io/en/stable/sdk/trace.export.html)；
Dashboard 配置依据 [Grafana provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/)。
