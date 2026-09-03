# ADR-0002：LangGraph 作为 Workflow Runtime

- 状态：Accepted / Phase-1 Agent Kernel verified
- 日期：2026-09-03
- 验证版本：`langgraph 1.2.11`（以 `uv.lock` 为准）

## 背景

Agent Workflow 需要显式状态、条件分支、有限循环、跨 HTTP 请求暂停/恢复、checkpoint
和运行时事件流。自研 Runner、状态保存和恢复协议会重复实现通用工作流能力，也会增加
竞态与故障恢复成本。

## 决策

使用 LangGraph Graph API 作为唯一 Workflow Runtime：

- `StateGraph` 定义 Node、Edge、条件路由和终止；
- checkpointer 保存 thread-scoped Graph State；
- `interrupt()` 与 `Command(resume=...)` 承担补槽暂停/恢复；
- 显式 `recursion_limit` 作为循环预算的最后保险；
- Runtime Adapter 消费 LangGraph 事件，再投影为稳定的内部 Trace 或 V2 SSE。

不使用高层预制 ReAct Agent。LangGraph 依赖只允许出现在 `workflow/`、checkpointer
adapter 和相应测试中；Domain、Policy 和 Tool Port 不导入 LangGraph。

阶段 0 使用稳定的事件协议 `v2`。安装版本的 `v3` 路径仍会发出 beta warning，待其
稳定并通过事件契约测试后再单独升级。

## 结果

- 优点：使用框架擅长的持久执行和 HITL 能力，减少自研调度代码；
- 优点：Node 仍是可注入、可单测的普通函数；
- 代价：Graph State、checkpoint 和流式事件存在版本迁移成本；
- 代价：interrupt 恢复会重新进入所在 Node，副作用必须幂等并放在独立节点。

## Phase-0 证据

- 三节点图可以编译并覆盖 direct 与 interrupt/resume 两条路径；
- 同一 `thread_id` 能恢复，两个 thread 的 checkpoint 相互隔离；
- 异步事件流可观察 `understand` 与 `complete` Node；
- 过小 `recursion_limit` 会确定失败；
- `/v1` 未挂载该图，现有行为不变。

## Phase-1 证据

- 正式 Agent 图包含 ingest、understand、decide、clarify、execute、validate、recover 和
  respond 节点，拓扑测试确认没有孤立节点；
- 条件路由、interrupt/resume、允许列表重试和业务预算均有确定终态；
- 从 `execute_tool` 前的历史 checkpoint 建立重放分支时命中执行收据，Gateway 未重复
  调用；
- State 继续通过严格 serializer 验证，领域层仍无 LangGraph import。

## 参考

- [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview)
- [Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)
- [Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
