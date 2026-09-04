# Phase 4D：V1 Tool 复用与五能力 Agent 闭环

> 状态：2026-09-04 已完成；本阶段使用本地 fixture，不依赖外部 API Key。
>
> 发布边界：V2 仍为显式装配能力，默认 `main.app` 和 `/v1/chat` 行为不变。真实政策
> RAG、价格 MySQL 和物流接口的生产配置不在本阶段内。
>
> 后续状态：五意图多轮黑盒 Runner、质量门禁和本地 fixture baseline 已在
> [Phase 5A](agent-kernel-phase5a.md) 完成。

## 1. 本阶段完成内容

Phase 4D 把已有政策 RAG Tool 与设备价格 Tool 接入 LangGraph Agent，同时避免复制检索、
引用校验、设备解析或价格匹配逻辑：

- 增加 `PolicyAssistantToolAdapter` 与 `DevicePriceAssistantToolAdapter`，把类型化
  `PolicyCommand` / `DevicePriceCommand` 转成现有 V1 `execute(question)` 调用；
- 抽取并复用 V1 `validate_tool_result`，保证 `/v1` 与 `/v2` 对工具身份、成功证据、
  空结果和证据类型使用同一份合同；
- 将 V1 `ToolResult` 投影为类型化 `AgentResult`，完整保留政策引用和设备价格候选，
  同时生成可校验的 `evidence_ids` 与 `SourceReference`；
- 为两类结果增加第二道 Agent Validator，拒绝缺失证据、来源错位、非法金额、无时区
  观察时间，以及非成功结果携带事实数据；
- 在 `WorkflowPolicy` 中补齐两类 Command 构造，在 Agent composition root 中支持借用
  V1 Tool，五种意图现在均能到达确定性的白名单工具；
- 将无网络 Demo 扩展为五能力，Policy/Device 使用真实业务 Tool + 本地 Source/Repository
  fixture，V1 和 V2 共用同一 Tool 实例；
- Agent Web 从只展示 evidence ID 升级为政策原文卡片与设备价格卡片，并只允许
  `http(s)` 来源链接。

## 2. 复用边界

```text
V2 PolicyCommand / DevicePriceCommand
                  |
                  v
        Compatibility Adapter
                  |
                  v
       existing V1 AssistantTool
       (RAG / parsing / matching)
                  |
                  v
             ToolResult
                  |
       shared V1 contract validator
                  |
                  v
 typed AgentResult + provenance -> LangGraph result validator
```

兼容层只负责协议翻译，不参与查询决策：

| 仍由现有 V1 Tool 负责 | 兼容层新增职责 |
| --- | --- |
| Policy RAG 调用、拒答映射、引用编号与 chunk 校验 | Command 到 question 的转换 |
| 设备品牌/型号/规格解析、只读查询、候选排序与截断 | ToolStatus 到 AgentResultStatus 的转换 |
| 业务回答、warning、reason code 和完整 Evidence | Evidence 到类型化 Agent Data/Provenance 的投影 |
| Source/Repository 的初始化、readiness 与关闭 | V1 异常到稳定 Agent Failure 的翻译 |

因此增加 V2 不会形成第二套政策检索或价格匹配实现；今后 V1 业务逻辑修复会被 V2 直接
复用。Adapter 也不反向导入具体 `PolicyKnowledgeTool` 或 `DevicePriceTool`，只依赖
对被包装工具只依赖 `AssistantTool` Port，便于合同测试和替换实现。

## 3. 生命周期所有权

V1 Tool 是借用依赖，不由 Agent runtime 重复初始化或关闭。组合顺序固定为：

1. FastAPI lifespan 初始化 V1 `ToolRegistry`；
2. Agent dependency factory 构建 SQLite/Graph，并把相同 Tool 实例包装后注册；
3. 请求期间 `/v1` 与 `/v2` 共用已初始化实例；
4. 先关闭 Agent 持久化资源，再由 V1 Registry 关闭 Tool。

这个规则避免 HTTP Client、数据库连接池被重复拥有或提前关闭。独立调用
`create_persistent_agent()` 并注入 V1 Tool 时，调用方必须显式承担这些 Tool 的生命周期。

## 4. 结果与失败映射

### 4.1 结果映射

| V1 状态 | Agent 状态 | 事实数据 |
| --- | --- | --- |
| `success` | `success` | 必须包含完整 Evidence 与一一对应 Provenance |
| `partial` | `partial` | 同上，同时保留 warnings |
| `need_more_info` | `need_more_info` | 不携带 Evidence，保留 missing fields |
| `no_match` | `no_match` | 不携带 Evidence |
| `error` | 不生成可缓存 Result | 转成显式 Failure，禁止当作业务完成 |

政策数据保留标题、原文 URL、摘要、文号、机构、章节、document/chunk ID 和检索分数；
设备价格数据保留品牌、型号、规格、当前/原价、币种、渠道、观察时间、在售状态、
product/SKU ID 与匹配分数。金额继续使用字符串/Decimal 语义，不引入浮点计算。

### 4.2 Failure 映射

| V1 异常或状态 | Agent Failure | 是否允许有界重试 |
| --- | --- | ---: |
| `ToolUnavailableError` | `upstream_unavailable` | 是 |
| 可识别 timeout | `upstream_timeout` | 是 |
| `PolicySourceError` / `PriceRepositoryError` | `upstream_unavailable` | 是 |
| `ToolContractError` / 投影无效 | `contract_violation` | 否 |
| V1 `error` 状态或未知异常 | `internal_error` | 否 |

Failure code 带固定 `legacy_<intent>_*` 前缀，不包含问题、型号、来源异常正文或凭据。
可重试错误仍由 LangGraph `recover` 节点统一受 Retry、Descriptor、deadline 和 recursion
预算约束；Adapter 自身不会再次请求，从而避免重试相乘。

## 5. 验收证据

本阶段新增 8 个 Python 自动化用例，覆盖：

1. 政策完整证据和 Provenance 投影；
2. 价格、原价和 SKU 字段无损映射；
3. unavailable / repository failure 分类与 retryable 语义；
4. 错误 Evidence 和错误 Command fail-closed；
5. Agent Validator 拒绝 Provenance 篡改；
6. LangGraph 对 Policy/Device 的确定性路由；
7. 同一 Tool 实例同时服务 V1 与 V2；
8. 五能力 Demo 的 capability discovery。

完整 Python workspace 为 `291 passed`。Web 新增 2 个安全投影测试，当前为
`17 passed`，`vue-tsc` 与 Vite production build 通过。

验证命令：

```bash
UV_CACHE_DIR=/private/tmp/intern26-uv-cache uv run pytest -q

cd apps/chat-web
npm run check:agent-types
npm test
npm run build
```

## 6. 尚未完成

- 默认生产 V2 挂载、Compose 灰度开关、readiness 与回退演练；
- 三类物流真实 Gateway Adapter；收到 URL、认证和响应合同后需要测试环境 API Key；
- 真实 Structured Model fallback；选择 Provider 后需要对应模型 API Key；
- 代表性五意图端到端 Eval、Wrong Tool Rate、Task Completion 和失败恢复率；
- 多副本共享 checkpointer、跨进程锁/租约和分布式 tracing。

上述 Phase 5A 与本地 Phase 5B 内容随后已完成。真实 Gateway 故障报告与 Phase 3B
仍需要先提供三类物流接口契约与测试凭据；逐 Node wall-clock span 和多副本运行属于
后续生产硬化。
