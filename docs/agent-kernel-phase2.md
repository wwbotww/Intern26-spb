# Phase 2：Hybrid Query Understanding 与 SQLite 持久化

- 状态：Implemented / engineering verified
- 日期：2026-09-03
- 前置提交：`07699e0`（Phase 0–1）
- 当前 API 边界：实现尚未挂载到 FastAPI；`/v1` 行为不变，V2 仍是 Proposed

## 1. 阶段结果

本阶段把 Phase 1 的单意图、内存态 Fake Tracking 垂直切片扩展为两个可独立验证的
闭环：

1. 五意图 Hybrid Query Understanding：规则优先，抽取邮件号、Decimal 重量和行政区
   候选；处理未知、多意图、跨轮补槽、意图切换、槽位更正、取消和重开；规则不足时
   才允许调用受 schema 约束的模型 Port。
2. 本地持久化 Runtime：`AsyncSqliteSaver` 保存 LangGraph checkpoint，独立 SQLite
   Repository 保存会话元数据、API 幂等收据和 Tool 执行收据；应用层对同一会话
   fail-fast 串行化，并提供 TTL 清理和 State v1 -> v2 迁移门禁。

真实轨迹、时限和资费接口尚未提供。因此只有 Fake Tracking Tool 可执行；其他意图会
完成理解与补槽，然后以 `capability_not_available` 安全 Handoff。现有 V1 政策和设备
价格工具尚未桥接到 Agent Registry，避免在 V2 契约稳定前改变线上路径。

## 2. 模块边界

| 模块 | 本阶段职责 | 明确不承担 |
| --- | --- | --- |
| `domain/understanding.py` | 版本化理解结果、候选意图、控制命令和模型输出 schema | 调模型、选工具 |
| `services/query_understanding.py` | 规则理解、Structured 模型闸门和 Hybrid fallback 顺序 | 外部接口调用、Graph 路由 |
| `services/region_resolver.py` | 可注入行政区目录、规范化候选和歧义结果 | 猜测歧义地名 |
| `services/slot_merger.py` | 同意图槽位合并、来源优先级和冲突保护 | 静默覆盖、跨意图复用 |
| `workflow/` | LangGraph 状态迁移、interrupt/resume、会话锁、TTL 和幂等编排 | 领域实体解析、数据库 SQL |
| `adapters/checkpointer_factory.py` | 严格序列化的内存/SQLite checkpointer 工厂 | 会话归属和业务状态复制 |
| `adapters/sqlite_persistence.py` | 元数据、API 幂等和 Tool 收据的 SQLite 实现 | Graph State 的第二份副本 |
| `eval/` | 独立 Agent Understanding 数据 schema 与公开样本 | 导入 Assistant 实现 |

架构测试继续保证 Domain 不依赖 FastAPI、LangGraph、数据库或外层模块，Application
Services 不反向依赖 Workflow/Adapter。LangGraph 导入只允许出现在 `workflow/` 和
checkpointer adapter。

## 3. Hybrid Query Understanding

### 3.1 决策顺序

```text
取消 / 重开控制命令
  -> 显式 UI Intent
  -> 活跃 Workflow Context
  -> 硬实体与关键词规则
  -> Structured Model fallback（最多一次）
  -> Pydantic schema gate
  -> deterministic WorkflowPolicy
  -> typed Tool Registry
```

规则已经明确识别 `policy`、`device_price`、`tracking`、`delivery_time` 和 `postage`。
规则命中、显式 UI 意图、活跃 Workflow 和控制命令都不会调用模型。模型只能返回
`StructuredModelUnderstanding` 中的公开字段，不能提交工具名、Node、Command、函数、
deadline 或执行参数；额外字段和非法联合会产生 `query_model_schema_invalid`，Hybrid
层 fail closed 到规则结果。

当前只定义 `StructuredQueryUnderstandingModel` Port 和 schema gate，没有绑定某个模型
供应商，也没有把 API Key 或 Prompt 注入 Graph State。等模型配置可用后，只需实现该
Port；Prompt 与 Parser 分别记录 `query-understanding-v1` 和
`structured-model-v1`，便于后续 A/B 与回滚。

模型选定意图后，规则提取器会按该意图重新提取邮件号、重量和地区；这些硬实体及其
缺失状态不直接采用模型自由生成值。这样模型负责补足语义分类，确定性代码仍负责进入
Tool Command 前的关键参数。

### 3.2 硬实体

- 邮件号：支持当前 provisional 的 13 位数字和 `AA123456789BB` 国际样式；日志/审计
  只记录掩码或参数指纹，最终规则仍需以真实轨迹接口契约修订。
- 重量：使用 `Decimal`，支持克、公斤、千克、g、kg 和斤；斤确定性换算为公斤；一次
  输入出现多个不同重量时进入澄清，不把第一个值直接用于工具。
- 行政区：`RegionRef` 同时保留原文、规范名、代码、解析状态和候选；“朝阳区”等重名
  返回 `ambiguous` 并要求补充上级地区。内置目录只是可替换的公开 Demo fixture，不是
  完整生产行政区数据。

### 3.3 跨轮合并与控制

`SlotMerger` 只合并同一 Intent：本轮没有提及的值从 Workflow State 保留，新值与已确认
值冲突时只记录字段名并继续使用旧值；未解析/歧义值可被后续澄清直接精化，只有
已确认值发生变更时才要求 `confirm_overwrite=true`。切换意图先
产生 `ClarifyIntentAction`，用户确认后清空旧 Intent 的 slots，再重新校验原问题。多意图
同样必须选择一项，一个 Agent Step 不会调用多个工具。

`取消`、`算了`、`退出`、`停止查询` 以及 `重新开始`、`重来`、`清空`、`重置` 在规则层
转换为 `ControlAction`，不调用模型或工具。控制完成后清理当前意图和槽位，并产生
`conversation_reset` 审计事件。

## 4. SQLite 持久化与恢复

本地组合根 `create_persistent_tracking_agent()` 同时管理两个生命周期：

- `AsyncSqliteSaver`：由 LangGraph 在 super-step 边界保存唯一 Graph State；
- Metadata/Receipt Repository：保存 owner、TTL、API 幂等结果及 Tool 执行收据，不复制
  Graph State。

`StatefulAgentService` 先验证 owner 和 TTL，再取得同会话运行权及
`(conversation_id, idempotency_key)` claim。相同 key + 相同 request hash 复用已完成
响应；相同 key + 不同 hash 返回 `STATE_CONFLICT`。失败的 run 会释放仍处于
`in_progress` 的 claim。Tool Executor 仍以
`(conversation_id, argument_fingerprint)` 先查成功收据，因此 checkpoint 节点重放和
HTTP 重试是两层独立防线。

### 4.1 重启恢复证据

集成测试执行如下真实生命周期，而不是在同一个内存 saver 上模拟：

```text
打开 SQLite -> 启动缺邮件号 Workflow -> interrupt -> 关闭全部连接
重新打开同一 SQLite -> 重新编译 Graph -> 同一 thread_id resume
-> Fake Tracking 调用一次 -> 完成 -> 重复 resume 返回幂等结果
```

SQLite 定位为本地、单进程 Demo。`ConversationRunCoordinator` 在单进程内对同一
conversation fail-fast 串行化；跨进程或多副本强一致不是当前声称能力，阶段 6 需选择
PostgreSQL/Redis 等后端并重新执行并发合同测试。

### 4.2 TTL 与迁移

- 默认会话 TTL 为 30 分钟，由组合根可配置；
- Janitor 与 Workflow Run 共享同一会话协调器；取得运行权后，先删除 LangGraph thread
  checkpoint，再删 API 幂等与 Tool 收据，最后写入 `deleted` tombstone；正在运行的会话
  本轮清理失败并留待重试，单个会话失败不会阻断其他会话；
- `AgentStateMigrator` 支持 v1 -> v2 的纯、加法迁移，为候选意图、控制、槽位来源、
  Prompt/Parser 版本等字段补默认值；未知未来版本返回
  `STATE_SCHEMA_INCOMPATIBLE`，不会尝试工具调用；
- Graph 新输入和 interrupt resume 都写入 `schema_version=2`。

## 5. 测试与评测证据

阶段 2 新增 37 项自动化测试；完整 Python workspace 当前 240 项通过。重点包括：

| 测试组 | 覆盖 |
| --- | --- |
| Query Understanding | 五意图、unknown、多意图、邮件号、Decimal 重量、行政区歧义、控制命令 |
| Structured fallback | 规则短路、单次模型调用、Prompt 版本、非法/越权输出 fail closed |
| 多轮 Graph | 意图选择、切换确认、补槽、槽位冲突确认、取消、未注册能力 Handoff |
| SQLite | 元数据/幂等重启、interrupt 重启恢复、重复 resume、并发冲突、TTL 清理 |
| State | v1 -> v2 加法迁移、未来版本拒绝、strict msgpack checkpoint |
| 架构 | Domain/Application 依赖方向、LangGraph 导入边界、V1/V2 隔离 |

公开评测 fixture `eval/datasets/agent-understanding-v1.jsonl` 包含 18 个场景、21 个标注
turn，并划分 calibration/holdout；当前 contract test 全部匹配。该小样本是回归夹具，
不是代表性模型质量结论，不能据此宣称生产 Intent Macro-F1 或 Slot F1。阶段 4 挂载 V2
后，Eval Runner 才通过 HTTP 黑盒执行它。

复现命令：

```bash
UV_CACHE_DIR=/private/tmp/intern26-uv-cache uv lock --check
LANGGRAPH_STRICT_MSGPACK=true .venv/bin/pytest -o addopts='' -q
```

## 6. 尚未完成

- 未配置真实 Structured LLM Adapter，也没有代表性离线/线上语料质量报告；
- Demo Region Catalog 不完整，邮件号规则和外部字段仍是 provisional；
- 轨迹、时限、资费真实 Gateway 属于阶段 3；
- 政策和设备价格 V1 Tool 尚未桥接到 Agent Registry；
- V2 JSON/SSE API、Web 多轮交互和前端 Renderer 属于阶段 4；
- SQLite 不作为多副本生产 checkpointer，尚未完成进程间锁、备份和灾难恢复；
- SQLite I/O 异常尚未统一映射为 `PERSISTENCE_UNAVAILABLE`，也未完成对应故障注入；
- 尚未建立 checkpoint 大小/数量指标与单 thread 硬上限，当前依赖短 TTL 整体回收；
- 尚未建设端到端 Trace、真实延迟、Macro-F1/Slot F1 与错误分析报告。

## 7. 外部依据

- [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)：
  checkpointer、thread 与异步持久化边界。
- [LangGraph SQLite 实现](https://github.com/langchain-ai/langgraph/tree/main/libs/checkpoint-sqlite)：
  `AsyncSqliteSaver` 的官方实现；本仓库锁定 `langgraph-checkpoint-sqlite 3.1.1`。
