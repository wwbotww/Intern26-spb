# AI 应用 / Agent 岗位素材：技术复盘

> 用途：整理简历、项目介绍和面试故事，不作为需求或运行手册。
>
> 范围：只记录当前技术 Demo 已实现、可验证的内容。示例数据和功能名称用于验证
> 模块能力，不代表完整真实业务场景；后续场景待需求稳定后另行补充。
>
> 代码基线：`offline-pipeline 0.2.0`、`rag-api 0.5.1`、
> `assistant-api 0.3.2`、`chat-web 0.2.0`、`eval 0.3.0`。

## 1. 30 秒技术介绍

这是一个围绕公开文档和结构化只读数据构建的 AI 应用 Demo。离线流水线处理网页、
附件、OCR、结构化切分和增量向量化；RAG 服务执行混合检索、Cross-Encoder 重排、
证据判断和引用约束生成；Assistant 用显式模式把 RAG 与结构化价格查询统一到一个
API；Vue Web 提供 SSE 交互；独立 Eval 包从调用方视角评测召回、拒答、引用、事实
覆盖、路由和延迟。

当前 Assistant 是确定性的单轮、单工具 Dispatcher，不应描述为具备自主规划、
服务端记忆或 Agent Loop 的完整 Agent。这个项目对 AI 应用 / Agent 岗位的价值，
主要在于 grounding、工具边界、结构化契约、评测、可靠性和可观测性实践。

## 2. 当前技术需求与负责范围

Demo 直接处理四类工程问题：

- 异构文档输入：多种页面模板、历史链接、PDF / Word / 表格和扫描件并存。
- RAG 质量控制：分别处理召回、重排、证据充分性、拒答和引用追踪。
- 异构只读工具：把政策 RAG 与结构化价格源接入统一协议，同时隔离凭证和权限。
- 可验证交付：提供鉴权、限流、健康检查、SSE、日志、指标、容器化和黑盒评测。

我的实现范围覆盖数据工程、RAG、应用编排、后端 API、前端接入、评测和部署配置。
文档不进一步推断这些模块最终会被组合成哪一种完整业务产品。

## 3. 已实现架构与模块边界

```text
offline-pipeline --write--> Milvus <--read-- rag-api
                                           ^
                                           | internal HTTP
chat-web --> assistant-api ----------------+
                 |
                 `--read-only--> structured price MySQL

eval ----------black-box HTTP----------> rag-api / assistant-api
```

| 模块 | 当前职责 | 可复用边界 |
| --- | --- | --- |
| `offline-pipeline` | 发现、解析、OCR、切分、向量化、质量报告与 Milvus 同步 | 数据源、OCR 和 sink 可替换；不依赖在线服务 |
| `rag-api` | 检索、重排、证据判断、回答与引用 | 独立无会话知识工具，可被不同上层应用调用 |
| `assistant-api` | 统一协议、显式路由、单工具执行和证据归一化 | 上游无需接触 Milvus、MySQL 或模型密钥 |
| `chat-web` | 输入、模式选择、SSE 和分类证据展示 | 只依赖 Assistant HTTP 契约 |
| `eval` | 黑盒数据集、指标、对比和复核队列 | 与服务实现解耦，可用于版本和阈值回归 |
| `contracts` | 离线写入与 RAG 读取共享的数据契约 | 防止 collection 和 embedding 配置漂移 |

关键约束：

- 在线 RAG 对 Milvus 只读，只有离线流水线可以写入。
- Assistant 对价格库只读；型号和规格匹配使用确定性代码，不让 LLM 决定价格事实。
- 服务间通过 HTTP 和显式 schema 连接，不跨应用导入实现。
- Eval 只走公开接口，验证调用方实际收到的行为。

## 4. 可量化证据

### 数据与索引

| 指标 | 结果 |
| --- | ---: |
| 页面正文 | 292 / 292 |
| 发现附件 | 144 |
| 成功归档附件 | 139 |
| 扫描附件 OCR | 38 个、500 页 |
| 最终 chunks | 12,163 |
| Dense 向量 | 768 维，L2 归一化 |
| 模型输入最大长度 | 419 tokens，低于 512 上限 |
| 增量向量化 | 复用 11,034 条，新计算 1,129 条 |

5 个源站历史附件无法恢复，但父文档、来源关系和失败状态被显式保留，没有静默
删除失败数据。

### 历史 RAG 评测基线

以下结果来自 `rag-api 0.5.0` 的 80 条样本（48 条可回答、32 条拒答），用于说明
评测方法，不代表当前 `0.5.1` 的生产 SLA：

| 指标 | 结果 |
| --- | ---: |
| Recall@5 | 1.0000，95% Wilson CI `[0.9259, 1.0000]` |
| MRR@5 | 0.8469 |
| 可回答问题错误拒答 | 0 / 48 |
| 无答案问题错误回答 | 0 / 32 |
| 引用 Gold 命中率 | 1.0000 |
| 必要事实覆盖率 | 0.9948 |
| 串行 Chat 延迟 | P50 6.47s，P95 8.97s |

24 条 holdout 并发测试的事实覆盖率为 0.9821，但 Chat P95 达到 40.67s，说明
CPU reranker 和生成链路的排队是明确的性能边界。

### 当前代码验证

代码基线 `1898261` 已完成：

- 145 个 Python 测试通过；有 1 个 Starlette / httpx 弃用警告。
- 7 个 Chat Web 测试通过，生产构建成功。
- Docker Compose 配置解析通过。

`assistant-api 0.3.1` 曾完成 10 条混合烟测并全部通过，但样本量小且早于当前版本，
只作为历史回归线索。

## 5. 有面试价值的技术故事

### 5.1 可恢复的数据流水线

用源接口声明总数检查分页完整性；抓取状态写入 SQLite；附件使用稳定 ID 和内容
哈希；OCR 通过 sidecar checkpoint 续跑；失败项进入质量报告。核心取舍是优先
保证完整性边界、lineage 和可恢复性，而不是隐藏失败来追求表面成功率。

### 5.2 模型感知的切分

原始 1,200 字符分块可能超过 `m3e-base` 的 512-token 上限。改为章节、条款和
表格感知切分后，用真实 tokenizer 扫描全部 embedding 输入，最终最大 419 tokens、
无超限，消除了静默截断风险。

### 5.3 分层证据决策

RAG 链路采用 Dense + BM25、RRF、Cross-Encoder、相关性门槛、LLM evidence
judge 和引用约束生成。检索与回答分开评测，并区分 `no_context`、
`reranker_rejected`、`llm_rejected`，因此可以定位失败层级，而不是只看最终文本。

### 5.4 LLM 与确定性逻辑分工

结构化价格查询先提取品牌、系列、型号和容量等字段，再执行硬约束过滤与
RapidFuzz 排序。硬字段冲突直接排除；LLM 不连接 MySQL，也不能把相似记录当成
准确事实。自然语言容错与金额类事实判断使用不同机制。

### 5.5 独立黑盒评测

Eval 覆盖召回、拒答、引用、事实覆盖、路由、候选 Recall、P50 / P95、并发、
基线对比、阈值扫描、Wilson 区间和人工 review queue。评测只调用 HTTP，避免
内部函数测试替代真实调用链。

### 5.6 Demo 的可靠性约束

服务具备 API Key、fail-closed 配置、限流、并发控制、live / ready 分离、结构化
日志、Prometheus、request ID、SSE 生命周期统计、非 root 和只读容器。Web 代理
在服务端注入 Assistant Key，浏览器不持有内部凭据。

## 6. 与 AI 应用 / Agent 岗位的能力映射

| 岗位能力 | 当前项目证据 | 表述边界 |
| --- | --- | --- |
| RAG / Grounding | 混合检索、重排、证据判断、引用和拒答 | 可描述为已实现 |
| Tool abstraction | `policy` / `device_price` 工具、统一 `ToolResult` 与 Evidence | 是固定工具编排，不是自主 Agent |
| Query parsing | 设备查询中的型号、系列、规格提取和校验 | 不是通用 Query Understanding |
| Routing | 客户端显式模式到单工具的确定性映射 | 没有模型自主路由 |
| Failure handling | 信息不足、无匹配、依赖不可用和契约失败分开处理 | 当前只覆盖单轮调用 |
| Evaluation | 黑盒质量、拒答、引用、候选、稳定性和性能指标 | 标注数据集规模仍有限 |
| Production awareness | 鉴权、限流、健康、指标、日志和容器安全 | 当前是单副本 Demo 基线 |

面试中应把它称为“AI 应用与受约束工具编排 Demo”。它具备建设 Agent 系统会用到
的若干基础能力，但当前没有状态化工作流、自动工具选择或 Agent Loop。

## 7. 简历 bullet 候选

- 设计并实现公开政策资料 RAG 工作区，打通 292 个页面、144 个附件、38 个扫描件 /
  500 页 OCR 到 12,163 个可追溯 chunk 的离线链路，支持断点恢复、质量报告和
  增量向量化。
- 构建 Dense + BM25、RRF、Cross-Encoder 与 LLM evidence judge 的分层检索
  回答链；在 80 条历史评测集上取得 Recall@5 1.00、MRR@5 0.8469，并评测拒答、
  引用、事实覆盖和尾延迟。
- 将 RAG 与结构化 MySQL 查询封装为统一 Assistant API，通过 typed ports /
  adapters、只读访问和确定性实体匹配约束模型权限及金额类幻觉。
- 完成 FastAPI + Vue + SSE Demo，加入鉴权、限流、健康检查、Prometheus、
  结构化日志和非 root 只读容器；当前基线通过 145 个 Python 测试与 7 个前端测试。

## 8. 面试问题索引

| 常见问题 | 可使用的证据 | 能体现的能力 |
| --- | --- | --- |
| 最难的数据问题 | 文件类型不一致、扫描件 OCR、失效附件留痕 | 数据质量、容错、lineage |
| 一次关键质量改进 | 用 tokenizer 发现并消除 chunk 静默截断 | 模型理解、测量驱动 |
| 如何减少幻觉 | 分层检索、evidence judge、引用约束与拒答 | RAG grounding |
| 为什么不全用 LLM | 型号硬约束和价格查询采用确定性匹配 | 安全边界、业务判断 |
| 如何证明效果 | 黑盒评测、holdout、Wilson CI、P95、review queue | Eval engineering |
| 发现了什么性能问题 | 并发下 reranker / 生成排队，准确率好但 P95 较高 | 容量分析、诚实复盘 |
| 模块如何复用 | HTTP 边界、typed ports / adapters、共享数据契约 | 架构与演进能力 |

## 9. 必须诚实说明的边界

- 这是技术 Demo，不代表已理解或覆盖某个真实业务的完整角色、流程和决策规则。
- 当前场景标签、示例问题和数据源用于验证模块，后续可能随需求调整。
- 当前已挂载的 Assistant `/v1` 只支持显式模式、单轮和单工具调用；隔离的 Agent
  Runtime 已实现自动意图理解、受限 Loop 和本地持久化，但尚未发布为 V2 API。
- OCR 和旧 Word 转换依赖 macOS，迁移 Linux 时需要替换适配器。
- Milvus 增量同步只插入缺失 chunk，旧版本回收仍需独立版本策略。
- 历史评测集规模有限；当前版本仍需补充困难负例和回归运行。
- CPU reranker 在并发下有明显尾延迟，不能把 Demo 结果当成生产容量结论。

## 10. 代码与证据入口

- [工作区架构](workspace-architecture.md)
- [Assistant API](assistant-api.md)
- [RAG 实现](rag-api.md)
- [评测说明](../eval/README.md)
- [部署说明](deployment.md)
- [Stateful Agent Workflow 下一阶段实施方案](agent-workflow-implementation-plan.md)
- [Phase 1 Agent Kernel 实现证据](agent-kernel-phase1.md)
- [Phase 2 Hybrid Understanding 与 SQLite 持久化证据](agent-kernel-phase2.md)
- `apps/offline-pipeline/src/spb_pipeline/`：采集、解析、OCR、切分、向量化和同步。
- `apps/rag-api/src/spb_rag_api/`：检索、重排、证据判断和回答服务。
- `apps/assistant-api/src/spb_assistant_api/`：显式路由、政策工具、价格匹配和统一协议。
- `eval/src/spb_eval/`：指标、runner、分析和报告。

## 11. LangGraph Agent 化设计与阶段证据

> 状态：总体方案仍处于 Proposed；阶段 0～2 已完成工程验证。当前已有正式
> `StateGraph` Agent Kernel、五意图 Hybrid Understanding、Region Resolver、Slot
> Merger、`AsyncSqliteSaver`、interrupt/resume、会话幂等/TTL/串行推进、类型化路由、
> 结果校验、有限重试与 Tool 重放收据。它证明的是本地单进程工程闭环和架构边界，不
> 代表生产多副本、真实业务接口、V2 API 或代表性质量指标已经完成。

该独立路径正在把当前单轮 Dispatcher 演进为受约束、可观测、可恢复的 Stateful Tool
Agent，以 LangGraph 作为核心 Workflow Runtime；下一阶段等待外部契约后接入邮件轨迹、
寄递时限和资费三个真实只读查询能力。详细实施基线见
[LangGraph Stateful Agent Workflow 实施方案](agent-workflow-implementation-plan.md)。

### 11.1 设计问题与目标

当前调用方必须显式选择 `policy` / `device_price`，工具只接收一个自然语言字符串，
服务端没有跨轮状态。新增场景需要解决：

- 从自由输入识别五类业务意图；
- 从多轮消息中收集邮件号、收寄地、寄达地和重量等字段；
- 对同名行政区、字段冲突、多意图和中途切换进行澄清；
- 字段完整后调用正确且唯一的只读工具；
- 面对超时、限流、非法响应和状态并发时安全恢复；
- 用黑盒评测和 Trace 证明路由与恢复行为，而不只展示最终聊天文本。

目标不是让模型自由规划所有动作，而是把不确定性限制在 Query Understanding，把工具
执行约束在 LangGraph 状态图、确定性 Policy、类型化 Command 和白名单中。

### 11.2 值得在面试中讲清楚的核心决策

#### 决策一：受约束 Agent，而不是自由 ReAct

业务查询的工具集合和必要字段都可以预先定义。开放式 ReAct 会增加错误工具调用、
重复调用、不可终止循环和难以回归的问题，因此采用 bounded workflow：

```text
Understand -> Clarify / Collect -> Route -> Execute -> Validate -> Respond
```

每轮最多执行有限 Step 和一个正常工具调用；缺少用户输入时进入可恢复的
`WAITING_USER`，超预算时明确结束。这个取舍体现的不是“少用 Agent”，而是根据
业务风险选择合适的自治范围。

#### 决策二：LangGraph 是编排运行时，不是业务规则容器

选择 LangGraph Graph API，是因为需求同时包含显式状态、条件分支、循环、跨请求暂停
和故障恢复。它负责 `StateGraph`、Node/Edge 调度、checkpointer、`interrupt()` /
`Command(resume=...)` 和运行时事件流；业务意图、槽位校验、Tool Registry、Failure
Taxonomy 与结果可信度仍由框架无关的 Domain / Policy / Tool Port 承担。

不再同时维护自研 Agent Runner 和全量 ConversationStore。LangGraph Checkpointer 是
thread-scoped 工作状态的唯一事实源，应用元数据仓储只保存 owner、TTL 和幂等收据。
这个边界既能使用框架擅长的 durable workflow，又保留可单测、可迁移的业务内核。

#### 决策三：Hybrid Query Understanding

采用以下优先级：

```text
显式 UI 意图
  > 当前 Workflow 上下文
  > 确定性实体提取和规则
  > Structured LLM fallback
```

邮件号、重量和行政区划等硬字段不依赖模型自由生成；模型只在规则不能可靠判断时
输出受 Pydantic / JSON Schema 约束的候选意图和槽位。Phase 2 已实现五意图规则、
硬实体、Structured Model Port/schema gate 和版本化 Prompt；尚未配置真实模型
Provider。低置信或候选接近时请求澄清，不把模型自报 confidence 当成真实校准概率。

#### 决策四：Query Understanding 与 Routing 分离

Query Understanding 可以输出 `IntentCandidate`、槽位、缺失字段和歧义，但不能返回
任意可执行函数。Deterministic Router 根据服务端 `ToolDescriptor` 把已验证 Intent
映射到固定工具。这样可以分别评测“理解是否正确”和“系统是否调用正确工具”，也能
阻止 Prompt Injection 直接越权调用工具。

#### 决策五：类型化 Command 和 Result

现有 `execute(question: str)` 适合单轮问题，但不适合轨迹、时限和资费。下一阶段用
`TrackingCommand`、`DeliveryTimeCommand`、`PostageCommand` 等判别联合承载参数，
并为轨迹时间线、时限估计和资费报价建立独立结果类型。

类型化边界带来三项收益：

- Tool Executor 不再重复解析自由文本；
- 外部 Adapter 字段变化不会直接泄漏到 API 和 Web；
- Eval 可以对参数、路由和结果不变量进行精确断言。

#### 决策六：显式 State、Reducer、Interrupt 和 Checkpoint

Agent State 只保存当前意图、已确认槽位、缺失字段、Workflow Phase、工具记录、预算
和错误摘要。Node 返回 partial update；LangGraph field reducer 处理累积字段，纯
`WorkflowPolicy` 处理业务转换。Event 只用于审计和 Trace，不额外建设一套平行的
Event-Sourcing Runtime。

单测使用 `InMemorySaver`，本地 Demo 已使用 `AsyncSqliteSaver`；生产后端在 Redis 与
PostgreSQL checkpointer 间通过 ADR 和压测选择。`conversation_id` 映射到 LangGraph
`thread_id`，补槽用 interrupt/resume 恢复。同一 thread 串行推进，结合
`Idempotency-Key`、argument fingerprint 和 Tool 执行收据防止节点重放造成重复调用；
TTL 和主动 reset 控制数据保留。

不保存模型思维链，也不把状态存储包装成长期用户记忆。Working Memory、RAG
Knowledge 和 Long-term User Memory 在设计中被明确区分。

#### 决策七：Failure 是状态图的一部分

错误不统一包装成“没有查询到”：

| 类别 | 设计行为 |
| --- | --- |
| 缺少字段 / 意图歧义 | 暂停并等待用户补充 |
| 输入非法 | 保留有效状态，指出具体字段 |
| 无业务结果 | 正常终态，不生成推测事实 |
| 超时 / 限流 / 暂时不可用 | 对只读调用有限重试或熔断 |
| 上游契约错误 | 阻止展示，不重试错误数据 |
| 状态冲突 | 重新加载或要求安全重试 |
| Checkpointer 故障 / State 版本不兼容 | 阻止推进，保留 thread 并走恢复或迁移流程 |
| Loop 超预算 | 强制终止并保留 Trace |

只对明确幂等、可恢复的错误重试。轨迹、资费等上游不可用时，模型不得生成近似事实；
降级必须来自另一个已注册且可验证的数据源。

#### 决策八：不记录 Chain of Thought，用结构化 Trace 解释行为

每次请求记录 Query Understanding 来源、候选意图、公开 signals、LangGraph Node/Edge、
interrupt/resume、checkpoint、工具名、尝试次数、校验结果和 finish reason。这样可以
定位失败步骤，同时避免记录模型内部推理、完整邮件号、凭据或高基数参数。

### 11.3 Agent 能力路线与当前证据

| 岗位能力 | 当前状态 | 下一份关键证据 |
| --- | --- | --- |
| Query Understanding | Phase 2：五意图规则、硬实体、Hybrid/schema gate、21-turn fixture | 真实模型 baseline、Macro-F1、Slot F1、错误分析 |
| Tool / Function Calling | Phase 1：Descriptor、类型化 Command、白名单、越权拒绝 | 真实五工具 Wrong Tool Rate |
| LangGraph Orchestration | Phase 1–2：StateGraph、条件边、interrupt/resume、重启恢复 | V2 黑盒 Graph Trace |
| Stateful Workflow | Phase 2：SQLite checkpoint、TTL、幂等、迁移、单进程锁 | 多副本 checkpointer 与跨进程冲突测试 |
| Agent Loop | Phase 1：Step/Tool/Retry/recursion 四层预算 | 代表性 Loop Step 分布与超预算率 |
| Memory Design | Phase 2：Working State、Metadata、Tool Receipt、RAG 分离 | 数据保留评审和生产清理演练 |
| Failure Handling | Phase 1–2：分类、有限重试、契约拒绝、Handoff、fail closed | 熔断与真实 Gateway 故障注入报告 |
| Human in the Loop | Phase 2：缺槽、多意图、切换和冲突确认 | V2 Web 可用性与任务完成率 |
| LLMOps / Observability | Prompt/Parser 已版本化；完整 Trace 未实现 | baseline/experiment 与 Dashboard |
| Agent Evaluation | 18 场景/21 turn 公开回归夹具；HTTP Runner 未接入 | V2 多轮黑盒 Completion/Recovery 报告 |
| Grounding | 现有 V1 RAG 引用与拒答可复用 | V2 Policy Tool 回归 |

阶段 0～2 当前已有的可复核证据：LangGraph 导入被架构测试限制在 Workflow Runtime /
Checkpointer Adapter 边界；状态图覆盖直接完成、缺槽/多意图中断、切换与更正确认、取消、
同 thread 重启恢复、thread 隔离、无匹配、有限重试、契约失败、超预算和 checkpoint
重放；类型化 Registry 拒绝任意工具名。SQLite 测试在关闭连接、重新编译 Graph 后恢复，
并验证 API 幂等、同会话并发拒绝、TTL 清理和 Tool 收据不重复调用 Fake Gateway。当前
新增 37 项阶段测试、完整 Python workspace 240 项通过；这些证据仍不替代真实工具、
生产持久化后端和代表性端到端质量报告。

### 11.4 推荐演示路径

最终面试 Demo 应覆盖正常路径和异常路径：

1. 输入完整邮件号，规则识别后直接查询轨迹；
2. 询问寄递时限但缺少寄达地，Graph 触发 interrupt，补槽后从同一 thread 恢复；
3. 输入“这个要多久”，候选意图不足，Agent 主动澄清；
4. 补槽过程中切换到资费查询，系统确认重置而不混用旧状态；
5. 上游第一次超时、第二次成功，Trace 展示 `recover` 分支和有限重试；
6. 上游返回 HTTP 200 但缺少必要字段，Validator 阻止不可信结果；
7. checkpoint 恢复或重复提交同一消息，执行收据避免重复 Tool Call；
8. Prompt Injection 要求调用未注册工具，Router 拒绝执行。

只展示 Happy Path 很难体现 Agent 工程能力；至少一半演示应覆盖歧义、恢复、契约错误
和安全边界。

### 11.5 面试故事模板

#### 故事 A：为什么没有使用自由 ReAct

- Situation：业务能力固定，但输入自然、字段可能跨轮补充；
- Task：既体现 Agent 能力，又保证不会调错工具或无限循环；
- Action：把不确定性放入 Query Understanding，用 LangGraph 显式状态图承载受限循环，
  执行仍由 typed Policy、工具白名单和预算控制；
- Result：完成后填写 Wrong Tool Rate、Loop 超预算率、Task Completion Rate 和故障
  恢复测试结果。

#### 故事 B：为什么选择 LangGraph，以及如何控制框架边界

- Situation：需求需要条件分支、补槽暂停、跨请求恢复和可观测执行路径；
- Task：避免重复造工作流运行时，也避免领域逻辑被框架 API 污染；
- Action：让 LangGraph 只承担 StateGraph、checkpointer、interrupt/resume 和事件流，
  Domain / Policy / Tool Port 保持无框架依赖，并通过 Node Adapter 接入；
- Result：完成后填写 Graph 分支覆盖率、中断/重启恢复用例、框架依赖架构测试和升级
  验证结果。

#### 故事 C：如何处理 Stateful Workflow

- Situation：HTTP 请求天然无状态，但补槽流程跨多轮且可能并发、重试或服务重启；
- Task：保证流程可恢复且不会重复调用工具；
- Action：使用 thread-scoped checkpointer、interrupt/resume、同会话串行推进、
  Idempotency-Key 和 Tool 执行收据；把元数据存储与 Graph State 分开；
- Result：完成后填写重启恢复、节点重放、重复请求、并发冲突和 TTL 清理的测试证据。

#### 故事 D：如何平衡规则和 LLM

- Situation：纯规则难覆盖自然表达，纯 LLM 又可能误识别硬字段；
- Task：提高理解覆盖率，同时保持可解释和可回归；
- Action：显式选择和状态优先，正则/词典处理硬实体，Structured LLM 只作为
  fallback，低置信时澄清；
- Result：完成后填写规则命中率、模型 fallback 率、Intent Macro-F1、Slot F1 和
  不必要澄清率。

#### 故事 E：如何让 Agent 安全失败

- Situation：上游接口和 checkpointer 都可能超时、不可用或返回不兼容数据；
- Task：避免把技术异常解释成无结果，更不能让模型填补事实或在恢复时重复调用工具；
- Action：建立 Failure Taxonomy、有限重试、按工具熔断、结果校验、执行收据、状态迁移
  和 Handoff；
- Result：完成后填写错误恢复率、契约错误拦截数、节点重放和故障注入用例通过率。

### 11.6 简历 bullet 模板

当前可以使用、但必须明确 `Phase-2 / Fake Tracking / local SQLite` 范围的工程表述：

- 基于 LangGraph `StateGraph` 实现可注入依赖的轨迹查询 Agent Kernel，以显式 Node、
  条件边和 `interrupt/resume` 编排补槽、执行、校验、恢复与响应；使用业务 Step、逻辑
  Tool Call、Retry 和 recursion limit 四层预算保证确定终止。
- 设计类型化 `Command -> Registry -> Tool -> Result Validator` 执行链，并以
  `(conversation_id, argument_fingerprint)` 执行收据处理 checkpoint 重放；离线测试从
  `execute_tool` 前历史 checkpoint 建立分支，确认 Fake Gateway 不发生重复调用。
- 实现五意图 Hybrid Query Understanding，以确定性邮件号、Decimal 重量和可注入行政区
  Resolver 处理硬实体，以 Slot Merger 保护跨轮冲突，并用版本化 Structured Model
  schema gate 阻止任意工具名；18 场景/21 turn 公开回归夹具全部匹配，明确该结果不是
  生产 Macro-F1。
- 构建 `AsyncSqliteSaver` + Metadata/API Idempotency/Tool Receipt 分层持久化，在关闭连接
  并重新编译 Graph 后恢复同一 interrupt；测试同时覆盖重复 resume、同会话并发拒绝、
  30 分钟可配置 TTL 清理和 v1 -> v2 State migration，完整 Python workspace 240 项通过。

以下 bullet 仍只有在真实接口、代表性评测和报告完成后才能使用；方括号内容必须替换为
真实数字：

- 在代表性 `[N]` 条评测集上对 Hybrid Query Understanding 完成 baseline/experiment，
  取得 Intent Macro-F1 `[X]`、Slot F1 `[Y]`、fallback 率 `[F]`，并将明确意图 Wrong
  Tool Rate 控制为 `[Z]`。
- 将本地 SQLite durable workflow 迁移到生产 checkpointer，在多副本 checkpoint 重放、
  重复提交、并发更新和服务重启演练中实现 `[结果]`，避免重复只读 Tool Call。
- 将政策 RAG、设备价格、邮件轨迹、寄递时限和资费封装为类型化只读工具，引入
  schema validation、有限重试、按能力熔断和错误分类，在 `[N]` 个故障注入场景中
  达到恢复率 `[X]`，并保持无业务结果与技术失败语义分离。
- 建立 Agent 多轮黑盒评测和 LangGraph 可观测链路，覆盖 Task Completion、
  Clarification、Routing、Recovery、Loop Steps、P95 延迟及 Prompt/Parser 版本对比，
  通过脱敏 Trace 定位 Node、条件边、interrupt、checkpoint 和 Tool Call。

### 11.7 指标回填表

| 指标 | Baseline | Experiment | 数据集 / 环境 | 证据路径 |
| --- | ---: | ---: | --- | --- |
| Intent Macro-F1 | 待测 | 待测 | 待补 | 待补 |
| Slot F1 | 待测 | 待测 | 待补 | 待补 |
| Wrong Tool Rate | 待测 | 待测 | 待补 | 待补 |
| Task Completion Rate | 待测 | 待测 | 待补 | 待补 |
| 不必要澄清率 | 待测 | 待测 | 待补 | 待补 |
| Failure Recovery Rate | 待测 | 待测 | 待补 | 待补 |
| 平均 / P95 Loop Steps | 待测 | 待测 | 待补 | 待补 |
| 端到端 P50 / P95 | 待测 | 待测 | 待补 | 待补 |
| Structured LLM fallback 率 | 待测 | 待测 | 待补 | 待补 |

没有代表性数据时保留“待测”，不得用设计门禁或单个演示样例替代真实结果。

### 11.8 当前与完成后的表述边界

现在可以准确表述：

- 完成了 LangGraph Stateful Agent Workflow 的需求分解、技术选型、schema、状态图、
  路由、持久化边界、失败处理、评测和阶段实施设计；
- 完成了阶段 0 LangGraph 技术验证：锁定依赖，运行最小 `StateGraph`，验证
  checkpoint、interrupt/resume、thread 隔离、事件流和图级步数上限，并用架构测试
  约束框架导入边界；
- 建立了 Intent、Slot、Query Understanding、Command、Result、Action、Failure 的
  Pydantic 契约和 Proposed V2 OpenAPI 草案，但尚未对外暴露 V2 路由；
- 实现了 Phase-1 Fake Tracking Agent Kernel：规则识别、缺槽 HITL、确定性工具路由、
  结果不变量校验、有限重试、预算终止和 checkpoint replay-safe 执行收据均有离线测试；
  该阶段里程碑定向测试 36 项、当时完整 Python 工作区测试 203 项通过；
- 实现了 Phase-2 五意图规则、Structured Model schema gate、Region Resolver、Slot
  Merger、多意图/切换/更正/控制，以及 SQLite checkpoint、会话幂等、TTL、串行推进和
  State 迁移；新增 37 项测试，当前完整 Python workspace 240 项通过；
- 建立 18 场景/21 turn 公开 Query Understanding 回归夹具并全部匹配，但尚不能把该小
  样本结果表述为代表性 Intent Macro-F1 或 Slot F1；
- 现有项目已经具备 RAG grounding、typed ports、只读工具、契约校验和黑盒评测
  基础；
- 已确定从单轮 Dispatcher 演进到受约束 Agent 的兼容路径。

当前不能表述：

- 已配置并评测真实 Structured LLM Provider；当前只有可注入 Port、版本化 Prompt 和
  schema gate；
- 已实现生产级多副本 LangGraph Agent 或跨进程会话锁；当前持久化结论只适用于本地
  单进程 SQLite；
- 已接入轨迹、时限和资费真实接口；
- 已达到实施方案中的质量门禁；
- 已完成 Redis、熔断、Agent Trace 或多轮评测。

各阶段验收完成后，应同步更新本文第 1、4、6、7、9、10 节，将对应项目从“设计”
迁移到“已实现证据”，并附上代码版本和评测报告。
