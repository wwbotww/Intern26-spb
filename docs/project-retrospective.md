# AI 应用 / Agent 岗位素材：技术复盘

> 用途：整理简历、项目介绍和面试故事，不作为需求或运行手册。
>
> 范围：只记录当前技术 Demo 已实现、可验证的内容。示例数据和功能名称用于验证
> 模块能力，不代表完整真实业务场景；后续场景待需求稳定后另行补充。
>
> 代码基线：`offline-pipeline 0.2.0`、`rag-api 0.5.1`、
> `assistant-api 0.3.3`、`chat-web 0.2.0`、`eval 0.5.0`。
>
> Agent 工程基线：Phase 2 提交 `186208f`；Phase 3A、4A～4D、5A、5B 工作树验证于
> 2026-09-04。

## 1. 30 秒技术介绍

这是一个围绕公开文档和结构化只读数据构建的 AI 应用 Demo。离线流水线处理网页、
附件、OCR、结构化切分和增量向量化；RAG 服务执行混合检索、Cross-Encoder 重排、
证据判断和引用约束生成；Assistant 用显式模式把 RAG 与结构化价格查询统一到一个
API；Vue Web 提供 SSE 交互；独立 Eval 包从调用方视角评测召回、拒答、引用、事实
覆盖、路由和延迟。

当前已发布的 Assistant `/v1` 是确定性的单轮、单工具 Dispatcher。仓库中的隔离
Agent Runtime 已基于 LangGraph 实现五意图理解、状态化补槽、受限 Loop、SQLite
恢复、五类白名单 Tool，以及 HTTP/退避/能力级熔断基础；Phase 4A/4B 已增加显式
装配的 V2 JSON/SSE 用户链路、三层幂等、owner 隔离、会话清理、OpenAPI 类型生成和
可刷新恢复的 Agent Web；Phase 4C 又加入独立 readiness、低基数指标、脱敏 Run Trace
与 lifespan janitor；Phase 4D 通过 compatibility Adapter 让政策/价格复用现有 V1
业务 Tool 与结果合同，五能力无网络 Demo 已闭环；Phase 5A 又通过公开 V2 HTTP 建立
13 场景/17 Turn 多轮评测、质量门禁和失败复核队列；Phase 5B 再加入不含业务值的
node/edge/checkpoint/interrupt/retry 语义 Trace、本地故障矩阵和严格同样本的 Agent
baseline/experiment 逐 Turn 对比。默认服务仍未发布 V2，也没有接入
真实物流接口。这个边界需要
在面试中主动说明。

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
| `chat-web` | V1 显式模式或 V2 Agent 会话、SSE、补槽和领域结果展示 | 生成类型 + 运行时校验，只依赖 Assistant HTTP 契约 |
| `eval` | RAG/Assistant/Agent 黑盒数据集、指标、门禁和复核队列 | 只走 HTTP，可用于版本与工作流回归 |
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

当前工作树（Phase 3A + 4A～4D + 5A，基于 `186208f`）已完成：

- 296 个 Python 测试通过；Phase 3A/4A/4B/4C/4D/5A 相对前序分别新增
  22/10/5/6/8/5 个 case。
- 17 个 Chat Web 测试通过，OpenAPI 生成类型校验和生产构建成功。
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
基线对比、阈值扫描、Wilson 区间和人工 review queue。Phase 5A 进一步按真实
conversation 顺序执行 Agent 多轮 Turn，计算 Intent、Required Input、Wrong Tool、
Task Completion、Recovery 和 API Error 门禁。评测只调用 HTTP，避免内部函数测试
替代真实调用链。Phase 5B 的报告对比进一步要求 dataset SHA256、完整 Gold 与门禁阈值
全部一致，并把缺失 Turn 和 API error 保留为逐 Turn 回归。

### 5.6 Demo 的可靠性约束

服务具备 API Key、fail-closed 配置、限流、并发控制、live / ready 分离、结构化
日志、Prometheus、request ID、SSE 生命周期统计、非 root 和只读容器。Web 代理
在服务端注入 Assistant Key，浏览器不持有内部凭据。

## 6. 与 AI 应用 / Agent 岗位的能力映射

| 岗位能力 | 当前项目证据 | 表述边界 |
| --- | --- | --- |
| RAG / Grounding | 混合检索、重排、证据判断、引用和拒答 | 可描述为已实现 |
| Tool abstraction | Agent 五类 Command/Tool；Policy/Device 通过 Port Adapter 复用 V1，三类物流使用 Gateway | 物流能力仍是 Fake Gateway |
| Query Understanding | Phase 2 五意图 Hybrid、硬实体、跨轮冲突与 schema gate | 未接真实模型 Provider |
| Routing | V1 显式路由；Agent 由确定性 Policy + Descriptor 白名单路由 | 不允许模型提交任意工具名 |
| Stateful workflow | LangGraph interrupt/resume、SQLite checkpoint、TTL、三层幂等、V2 JSON/SSE、Web 刷新恢复与删除 | 仅本地单进程，默认服务未发布 V2 |
| Failure handling | 分类、有限重试、结果拒绝、受限 Retry-After、能力级熔断；Phase 5B 本地故障矩阵 | 真实接口故障注入仍待完成 |
| Evaluation | Phase 5A 多轮 V2 HTTP 与七项门禁；Phase 5B 同样本对比和逐 Turn 回归 | 当前 Agent 数据集是小型 fixture |
| Production awareness | 鉴权、限流、健康、指标、日志和容器安全 | 当前是单副本 Demo 基线 |

面试中可把整体称为“AI 应用与受约束 Agent 工程 Demo”，但必须区分已发布 V1 与隔离
Agent Runtime：后者已具备状态化工作流、自动意图路由、bounded loop、五能力本地闭环和
可注入 V2 JSON/SSE + Web 用户链路、运行级摘要和细粒度语义 Trace，尚不具备真实物流
Adapter、逐 Node 分布式耗时 span 或多副本能力。

## 7. 简历 bullet 候选

- 设计并实现公开政策资料 RAG 工作区，打通 292 个页面、144 个附件、38 个扫描件 /
  500 页 OCR 到 12,163 个可追溯 chunk 的离线链路，支持断点恢复、质量报告和
  增量向量化。
- 构建 Dense + BM25、RRF、Cross-Encoder 与 LLM evidence judge 的分层检索
  回答链；在 80 条历史评测集上取得 Recall@5 1.00、MRR@5 0.8469，并评测拒答、
  引用、事实覆盖和尾延迟。
- 将 RAG 与结构化 MySQL 查询封装为统一 Assistant API，通过 typed ports /
  adapters、只读访问和确定性实体匹配约束模型权限及金额类幻觉。
- 完成 FastAPI + Vue 双版本 SSE Demo，加入鉴权、限流、健康检查、Prometheus、
  结构化日志、非 root 只读容器及 OpenAPI 类型生成；当前基线通过 296 个 Python 测试
  与 17 个前端测试。
- 构建仅依赖 V2 HTTP 的 Agent 多轮 Eval，以 conversation 级顺序执行验证
  interrupt/resume；本地 13 场景/17 Turn fixture 中 Intent、Task Completion 和
  Recovery 均为 1.0000，Wrong Tool/API Error 为 0，明确该数字不是生产准确率。

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
| 首次请求如何幂等 | 创建/消息/Tool 三层收据与跨重启测试 | Stateful Workflow、分层故障模型 |
| 如何控制框架泄漏 | V2 只依赖 Service Protocol，Graph State 经 DTO 投影 | LangGraph 边界、API 演进 |
| SSE 如何避免绑定框架 | 公开停止态投影为版本化事件，前端生成类型后再运行时校验 | Streaming Contract、兼容性 |
| 如何做 Agent 可观测性 | 固定标签指标 + 哈希 Run Trace + 白名单 node/edge 语义时间线 | LLMOps、隐私与基数控制 |
| 过期会话如何治理 | lifespan janitor、共享 coordinator、timeout 与 degraded 状态 | Stateful Operations |
| 如何复用旧系统能力 | V1 Tool Port compatibility Adapter、共享合同和单一生命周期所有者 | 渐进式架构演进 |
| 如何证明 Agent 行为 | 多轮 HTTP Runner、七项质量门禁、失败仍落盘与 review queue | Agent Eval、测量驱动 |
| 如何避免实验“伪提升” | 强制同 dataset hash、同 Gold、同门禁并输出逐 Turn 回归 | 实验设计、可复现性 |

## 9. 必须诚实说明的边界

- 这是技术 Demo，不代表已理解或覆盖某个真实业务的完整角色、流程和决策规则。
- 当前场景标签、示例问题和数据源用于验证模块，后续可能随需求调整。
- 当前已挂载的 Assistant `/v1` 只支持显式模式、单轮和单工具调用；隔离的 Agent
  Runtime 已实现自动意图理解、受限 Loop、本地持久化和可注入 V2 JSON/SSE + Web，
  Phase 4C 已补运行级 readiness/metrics/脱敏 Trace，Phase 4D 已补五能力本地闭环，
  Phase 5A 已补多轮 HTTP Eval 与本地质量门禁，Phase 5B 已补 node/edge 语义 Trace、
  本地故障矩阵和报告对比；但默认 production composition root 尚未发布 V2，也没有
  逐 Node 分布式耗时 span、真实 Gateway 故障报告或多副本语义。
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
- [Phase 3A Gateway 与可靠性基础证据](agent-kernel-phase3a.md)
- [Phase 4A Stateful Agent V2 JSON API 证据](agent-kernel-phase4a.md)
- [Phase 4B Versioned SSE 与 Stateful Agent Web 证据](agent-kernel-phase4b.md)
- [Phase 4C Agent Operations 与隐私安全可观测性证据](agent-kernel-phase4c.md)
- [Phase 4D V1 Tool 复用与五能力 Agent 闭环证据](agent-kernel-phase4d.md)
- [Phase 5A Agent 多轮黑盒评测与质量门禁证据](agent-kernel-phase5a.md)
- [Phase 5B 可靠性故障矩阵、语义 Trace 与 Agent 报告对比证据](agent-kernel-phase5b.md)
- `apps/offline-pipeline/src/spb_pipeline/`：采集、解析、OCR、切分、向量化和同步。
- `apps/rag-api/src/spb_rag_api/`：检索、重排、证据判断和回答服务。
- `apps/assistant-api/src/spb_assistant_api/`：显式路由、政策工具、价格匹配和统一协议。
- `eval/src/spb_eval/`：指标、runner、分析和报告。

## 11. LangGraph Agent 化设计与阶段证据

> 状态：总体方案仍处于 Proposed；阶段 0～2、3A、4A～4D、5A 与本地 5B 已完成。当前已有正式
> `StateGraph` Agent Kernel、五意图 Hybrid Understanding、Region Resolver、Slot
> Merger、`AsyncSqliteSaver`、interrupt/resume、会话幂等/TTL/串行推进、类型化路由、
> 结果校验、Tool 重放收据、时限/资费 Tool、有限退避与能力级熔断，以及显式装配的
> V2 JSON/SSE、owner/删除、生成类型、运行时校验、Web 恢复、readiness、低基数指标、
> 脱敏 Run Trace、janitor 边界、复用 V1 Policy/Device Tool 的兼容层、只经 V2
> HTTP 的多轮 Eval 与质量门禁，以及 checkpoint 增量语义 Trace 和 Agent 同样本对比。
> 它证明的是本地
> 单进程工程闭环和架构边界，不代表生产多副本、真实业务接口、完整分布式 Trace 或代表性
> 质量指标已经完成。

该独立路径正在把当前单轮 Dispatcher 演进为受约束、可观测、可恢复的 Stateful Tool
Agent，以 LangGraph 作为核心 Workflow Runtime；本地 Phase 5B 已完成，下一步可在
不依赖外部接口的前提下继续逐 Node span/部署硬化，Phase 3B 则等待接口后接入三个真实
只读查询能力。详细实施基线见
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

现有 `execute(question: str)` 适合单轮问题，但不适合轨迹、时限和资费。Agent 路径已用
`PolicyCommand`、`DevicePriceCommand`、`TrackingCommand`、`DeliveryTimeCommand`、
`PostageCommand` 判别联合承载参数，并建立独立结果类型；政策/价格通过兼容层复用
V1 Tool，时限和资费目前通过 Fake Gateway 验证，wire schema 留给真实 Adapter。

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

#### 决策八：传输只尝试一次，恢复预算由 Graph 统一拥有

如果 HTTP Client 和 Workflow 都各自重试，总调用次数会相乘，checkpoint 重放时也更难
解释。因此 Phase 3A 让共享 HTTP 层一次只发一个请求，由 LangGraph `recover` 节点统一
检查 Failure allowlist、重试次数、Descriptor 尝试次数、Action deadline 和退避计划。
`Retry-After` 超过本地等待上限时直接终止本轮，不长期占用运行槽；熔断器按 capability
隔离，并处理被取消的 half-open probe。这个设计把“重试、重放、熔断”三个相近概念
分开：重试处理瞬态失败，执行收据处理 checkpoint 重放，熔断保护持续故障的依赖。

#### 决策九：把幂等拆成创建、消息和工具执行三层

`conversation_id=null` 是经常被忽略的幂等缺口：如果只按
`(conversation_id, Idempotency-Key)` 保存响应，客户端在首次响应丢失后没有
conversation ID，重试会创建第二个 Workflow。Phase 4A 增加独立创建收据，在同一
SQLite 事务中把 `(owner_id, key, request_hash)` 绑定到服务端随机 UUID；随后消息收据
防止重复推进，argument fingerprint Tool 收据防止 checkpoint 重放再次调用上游。

三层分别处理“创建重放、HTTP 消息重放、图节点重放”，同 Key 不同 hash 一律冲突。
跨进程生产实现仍需把这些唯一约束迁移到共享数据库，不能只依赖进程内缓存。

#### 决策十：不记录 Chain of Thought，用结构化 Trace 解释行为

Phase 4C 先让每次 Graph Run 记录公开 phase、intent、next action、缺失字段名、结果/失败
摘要、耗时，以及 conversation/turn UUID 的哈希引用。它不记录用户原文、槽位值、结果
data、Graph State、checkpoint 或 Chain of Thought。Phase 5B 再从运行前后 checkpoint
提取本次新增的显式 `AgentEvent`，以固定白名单输出 Node/Edge、interrupt/resume、工具
尝试、校验、恢复和计数；未知 detail 丢弃、ID 哈希、事件有界，仍不转发 LangGraph
debug event。逐 Node wall-clock span 与生产采样尚未实现。

#### 决策十一：SSE 是公开状态投影，不是 LangGraph 调试事件转发

直接把 `astream_events` 发到浏览器会把 node 名、内部 State 和框架版本变成公开契约，
也会使前端随着图重构而破坏。Phase 4B 改为固定的 `status -> state ->
input_required|result -> delta -> done` 投影，并用 `error` 表达建流后的失败。OpenAPI
生成 TypeScript 类型，浏览器对已知事件再做运行时校验；未知事件可忽略，已知事件版本
或结构错误则失败关闭。JSON 与 SSE 排除传输字段后共享业务指纹，因此断流可用原幂等键
重放而不重复推进工作流。

#### 决策十二：用 Port Adapter 渐进复用 V1，而不是重写业务 Tool

政策 RAG 和价格匹配已经具备成熟的 V1 Tool、拒答/证据约束和只读数据边界。Phase 4D
新增的兼容层只接受 `AssistantTool` Port，把类型化 Command 转成 question，再把
`ToolResult` 投影为完整 `AgentResult` 和 Provenance；V1 Dispatcher 与 V2 Adapter 调用
同一个结果合同校验函数。Tool 实例由 V1 Registry 唯一初始化和关闭，Agent 只借用，
避免 HTTP Client 或连接池出现双重所有权。这既保留已有回归证据，也让未来业务修复同时
覆盖 V1/V2。

#### 决策十三：Agent 实验必须同样本、同 Gold、同门禁比较

只比较两份报告中的总体准确率，无法排除样本变化或失败 Turn 被漏计。Phase 5B 的
`agent-compare` 因此先校验 dataset SHA256、完整场景/Turn Gold 和质量门禁阈值；任一
不一致直接拒绝。通过后从逐 Turn observation 重算 summary，再输出固定方向的核心指标
与逐 Turn regression/improvement，
把 API error、缺失 Turn、理解、补槽、路由和结果合同失败保留在证据中。这使规则
baseline 与后续 Structured Model experiment 可以复现，但当前尚无真实模型报告。

### 11.3 Agent 能力路线与当前证据

| 岗位能力 | 当前状态 | 下一份关键证据 |
| --- | --- | --- |
| Query Understanding | Phase 5A：五意图规则 + 17 Turn V2 黑盒 Intent/补槽门禁 | 真实模型 baseline、Macro-F1、Slot F1、错误分析 |
| Tool / Function Calling | Phase 5A：五查询白名单；本地 Result Wrong Tool 0/11 | 真实五工具 holdout Wrong Tool Rate |
| LangGraph Orchestration | Phase 5B：StateGraph 条件路径、interrupt/resume/checkpoint/Tool retry 脱敏语义 Trace | 逐 Node wall-clock span 与生产采样 |
| Stateful Workflow | Phase 4C：SQLite checkpoint、TTL、三层幂等、owner、删除、API/Web 恢复、janitor | 多副本 checkpointer 与跨进程冲突测试 |
| Agent Loop | Phase 5B：Step/Tool/Retry/recursion 四层预算进入 Trace 与本地超预算矩阵 | 代表性 Loop Step 分布与超预算率 |
| Memory Design | Phase 2：Working State、Metadata、Tool Receipt、RAG 分离 | 数据保留评审和生产清理演练 |
| Failure Handling | Phase 5B：分类、退避、Retry-After、熔断、契约拒绝与本地故障矩阵 | 真实 Gateway 故障注入报告 |
| Human in the Loop | Phase 5A：4 个补槽/多意图场景经 V2 HTTP 恢复 4/4 | 代表性任务完成率 |
| LLMOps / Observability | Phase 5B：低基数 Run 指标、独立 readiness、停止态摘要 + node/edge 语义 Trace | wall-clock span、OpenTelemetry 与 Dashboard |
| Agent Evaluation | Phase 5B：13 场景/17 Turn V2 HTTP、七项门禁、review queue 与同样本逐 Turn 对比 | 真实模型/接口 holdout |
| Grounding | Phase 4D：V1 RAG 引用/拒答通过完整 Evidence Adapter 进入 V2 | 真实 RAG V2 黑盒回归 |

阶段 0～5B 当前已有的可复核证据：LangGraph 导入被架构测试限制在 Workflow Runtime /
Checkpointer Adapter 边界；状态图覆盖直接完成、缺槽/多意图中断、切换与更正确认、取消、
同 thread 重启恢复、thread 隔离、无匹配、有限重试、契约失败、超预算和 checkpoint
重放；类型化 Registry 拒绝任意工具名。SQLite 测试在关闭连接、重新编译 Graph 后恢复，
并验证 API 幂等、同会话并发拒绝、TTL 清理和 Tool 收据不重复调用 Fake Gateway。
Phase 3A 又验证三查询 Tool 路由、HTTP 故障归一、退避预算、能力熔断隔离与半开恢复；
相对 Phase 2 新增 22 个 case。Phase 4A 又从 HTTP 黑盒验证创建/补槽/恢复/新 turn、
跨重启创建重放、owner 隔离、TTL、删除、外层 timeout 与错误映射，新增 9 个 API case
和 1 个架构 case。Phase 4B 增加版本化 SSE、建流前/后失败语义、跨传输幂等、生成类型、
前端运行时校验和 lifespan 测试；浏览器验证轨迹直接完成、缺槽刷新恢复、时限/资费
Renderer 及多意图选择。Phase 4C 又验证 V2 readiness fail-closed、固定标签指标、Trace
脱敏和 janitor 周期/降级行为。Phase 4D 又验证 V1/V2 共享 Tool、完整证据投影、错误分类、
Provenance 防篡改和五能力路由。Phase 5A 再从 Eval 进程只经公开 HTTP 验证 13 场景 /
17 Turn、五意图、4 次多轮恢复和 11 个结果路由，七项本地 fixture 门禁全部通过；
Phase 5B 新增 12 个测试 case，验证语义 Trace 的隐私/路径/恢复投影，以及 Agent 报告的
同数据、同 Gold、同门禁约束和逐 Turn 差异。当前完整 Python workspace `308 passed`、
Web `17 passed`，类型生成检查与 production build 通过。
这些证据仍不替代真实工具、生产持久化后端和代表性端到端质量报告。

### 11.4 推荐演示路径

最终面试 Demo 应覆盖正常路径和异常路径：

1. 输入完整邮件号，规则识别后直接查询轨迹；
2. 询问寄递时限但缺少寄达地，Graph 触发 interrupt，补槽后从同一 thread 恢复；
3. 输入“这个要多久”，候选意图不足，Agent 主动澄清；
4. 补槽过程中切换到资费查询，系统确认重置而不混用旧状态；
5. 上游第一次超时、第二次成功，Trace 展示 `recover` 分支和有限重试；
6. 上游返回 HTTP 200 但缺少必要字段，Validator 阻止不可信结果；
7. checkpoint 恢复或重复提交同一消息，执行收据避免重复 Tool Call；
8. Prompt Injection 要求调用未注册工具，Router 拒绝执行；
9. 政策请求走已有 RAG Tool，并在 V2 卡片展示可追溯引用；
10. 设备请求走已有确定性匹配 Tool，完整展示价格、规格、来源和观察时间。

只展示 Happy Path 很难体现 Agent 工程能力；至少一半演示应覆盖歧义、恢复、契约错误
和安全边界。

### 11.5 面试故事模板

#### 故事 A：为什么没有使用自由 ReAct

- Situation：业务能力固定，但输入自然、字段可能跨轮补充；
- Task：既体现 Agent 能力，又保证不会调错工具或无限循环；
- Action：把不确定性放入 Query Understanding，用 LangGraph 显式状态图承载受限循环，
  执行仍由 typed Policy、工具白名单和预算控制；
- Result：Phase 5A 本地 fixture 的 11 个结果路由 Wrong Tool 为 0，10 个目标任务全部
  完成；Phase 5B 本地故障 Trace 可观察 Step/Tool/Retry 和超预算终态，代表性 Loop 分布
  与真实接口恢复率仍待后续报告。

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
  会话创建/消息/Tool 三层幂等；把元数据存储与 Graph State 分开；
- Result：已验证 API 创建与恢复跨重启重放、Tool 不重复调用、并发冲突、TTL 和删除；
  多副本共享存储证据仍待完成。

#### 故事 D：如何平衡规则和 LLM

- Situation：纯规则难覆盖自然表达，纯 LLM 又可能误识别硬字段；
- Task：提高理解覆盖率，同时保持可解释和可回归；
- Action：显式选择和状态优先，正则/词典处理硬实体，Structured LLM 只作为
  fallback，低置信时澄清；
- Result：本地 V2 黑盒 fixture 的 Intent 为 17/17、required input 为 4/4且无不必要
  澄清；样本小且未接模型，不能表述为 Macro-F1、Slot F1 或生产准确率。

#### 故事 E：如何让 Agent 安全失败

- Situation：上游接口和 checkpointer 都可能超时、不可用或返回不兼容数据；
- Task：避免把技术异常解释成无结果，更不能让模型填补事实或在恢复时重复调用工具；
- Action：建立 Failure Taxonomy、有限重试、按工具熔断、结果校验、执行收据、状态迁移
  和 Handoff；
- Result：Phase 3A/4A 分别新增 22/10 个自动化 case，Phase 4B 再增加 SSE/lifespan 与
  Web contract 测试，Phase 4C 增加 6 个 operations/隐私用例，Phase 4D 增加 8 个
  Tool 复用/故障合同用例，Phase 5A 增加 5 个 Eval/门禁用例，Phase 5B 增加 12 个
  Trace/故障/报告对比用例；本阶段全量为 Python `308 passed`、Web `17 passed`；
  真实 Gateway 错误恢复率、烟测和线上指标仍待接口接入后填写。

#### 故事 F：如何让新 Agent 复用旧系统而不复制逻辑

- Situation：政策 RAG 与价格匹配已在 V1 稳定运行，Agent 又需要类型化 Command、Result
  和 Failure；直接重写会产生两套证据与匹配规则；
- Task：保持 V1 兼容，同时让五意图都进入同一 LangGraph 路由；
- Action：以 `AssistantTool` Port 建 compatibility Adapter，共享 V1 合同校验，只做
  Command/Result/Failure 投影，并明确 V1 Registry 是 Tool 生命周期唯一所有者；
- Result：8 个新增用例验证完整 Evidence、错误分类、Provenance 防篡改、五能力路由和
  V1/V2 共用实例；Phase 5A/5B 又补齐公开多轮门禁和同样本回归定位。

#### 故事 G：如何证明 Agent 不只是能演示

- Situation：单元测试和 Happy Path UI 无法证明调用方真正经历了 conversation、
  interrupt/resume 和结果投影；
- Task：建立可复现、能阻断回归且失败后仍保留诊断证据的评测门禁；
- Action：Eval 只走 V2 HTTP，以场景并发、Turn 串行的方式复用 conversation；独立校验
  response schema，计算 Intent/补槽/Wrong Tool/Completion/Recovery/API Error，并把
  dataset SHA256、阈值、分母和 review queue 写入报告；
- Result：13 场景/17 Turn 本地 fixture 七项门禁通过；首次运行还发现未知 Handoff 的
  Gold 应为公开 `intent: null`，说明评测能用于契约校准。该数字不外推到生产。

#### 故事 H：如何解释 Agent 路径但不暴露思维链

- Situation：终态日志不能说明为什么重试，原始 LangGraph event 又可能泄露 State、Prompt
  和工具参数；
- Task：让一次 Workflow 可定位，同时保持公开协议和隐私边界稳定；
- Action：Node 只写类型化语义事件，Runtime 对比运行前后 checkpoint，投影固定白名单的
  node/edge/interrupt/resume/checkpoint/tool/retry Trace；日志仅使用哈希会话引用，Sink
  失败不影响业务；
- Result：Phase 5B 用 timeout 恢复、重试耗尽、契约漂移、Loop Budget 和 HITL 用例验证
  路径及隐私边界；后续仍需补逐 Node span、采样和真实接口 trace。

### 11.6 简历 bullet 模板

当前可以使用、但必须明确 `Phase 5B / Fake Gateway / local fixture / local SQLite / opt-in V2`
范围的工程表述：

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
  30 分钟可配置 TTL 清理和 v1 -> v2 State migration。
- 将轨迹、时限、资费拆为独立 Command/Tool/Gateway，在 LangGraph 中执行确定性白名单
  路由；建立单次 HTTP、稳定错误分类、有界 jitter、受限 Retry-After 和按能力熔断，
  Phase 3A 新增 22 个测试 case。
- 为 Stateful Agent 实现显式装配的 V2 JSON API，将 LangGraph interrupt 投影为
  `required_inputs/next_action`；以创建、消息、Tool 三层持久化收据分别处理首次请求、
  HTTP 重放和 checkpoint 重放，并验证 owner 隔离、跨重启恢复、TTL 与幂等删除；
  Phase 4A 新增 9 个 API case 和 1 个架构 case，完整 Python workspace 272 项通过。
- 为 V2 设计版本化 SSE Projection，隔离 LangGraph 内部事件；从 OpenAPI 确定性生成
  TypeScript 类型并对 wire payload 二次运行时校验，Web 支持五入口、HITL 补槽/多意图
  选择、刷新恢复、幂等断流重试和轨迹/时限/资费 Renderer；Phase 4B 后完整 Python
  workspace 277 项、Web 15 项通过，并完成桌面与 390 × 844 浏览器 E2E。
- 为 Stateful Agent 增加独立 readiness、固定枚举 Prometheus 指标和版本化脱敏 Run
  Trace，避免 conversation/request ID 与业务字段成为高基数标签；用 FastAPI lifespan
  管理 TTL janitor 的启动、周期、timeout、降级和关闭，并以 6 个新增用例覆盖 fail-closed
  与隐私边界。
- 以 Port compatibility Adapter 将既有政策 RAG 与设备价格 Tool 接入 LangGraph，复用
  V1 拒答、引用校验和确定性价格匹配；建立共享结果合同、类型化 Evidence/Provenance 与
  稳定 Failure 映射，并用 8 个新增测试验证 V1/V2 共用实例和五能力确定性路由。
- 建立仅通过公开 V2 HTTP 的多轮 Agent Eval，按场景并发、Turn 串行复用 conversation，
  独立校验响应契约并生成七项 CI 门禁和 review queue；13 场景/17 Turn 本地 fixture
  中 Wrong Tool/API Error 为 0、Task Completion 10/10、Recovery 4/4。
- 为 LangGraph Run 构建固定白名单语义 Trace，从 checkpoint 增量还原 Node/Edge、
  interrupt/resume、Tool attempt、Retry 和 Loop 计数；ID 仅写哈希引用，拒绝 Prompt、
  槽位值、结果正文和任意 detail，并用本地故障矩阵验证安全失败路径。
- 为 Agent Eval 增加严格同样本 baseline/experiment 对比，强制 dataset SHA256、完整
  Gold 和门禁阈值一致，输出核心指标方向与逐 Turn regression/improvement，防止因换
  样本或漏掉 API error 产生虚假提升。

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
- 扩展 Agent 评测到代表性 holdout、故障注入和 baseline/experiment 对比，补齐 Loop
  Steps 与 Prompt/Parser 版本维度，并通过脱敏 Trace 定位 Node、条件边、interrupt、
  checkpoint 和 Tool Call。

### 11.7 指标回填表

| 指标 | Baseline | Experiment | 数据集 / 环境 | 证据路径 |
| --- | ---: | ---: | --- | --- |
| Intent Macro-F1 | 待测 | 待测 | 待补 | 待补 |
| Slot F1 | 待测 | 待测 | 待补 | 待补 |
| Intent Accuracy | 1.0000（17/17） | 待测 | Phase 5A 本地 fixture | `eval/baselines/phase5a-local-fixture-v1` |
| Required Input Accuracy | 1.0000（4/4） | 待测 | Phase 5A 本地 fixture | 同上 |
| Wrong Tool Rate | 0.0000（0/11） | 待测 | Phase 5A 本地 fixture | 同上 |
| Task Completion Rate | 1.0000（10/10） | 待测 | Phase 5A 本地 fixture | 同上 |
| 不必要澄清率 | 0.0000（0/17） | 待测 | Phase 5A 本地 fixture | 同上 |
| Recovery Rate | 1.0000（4/4） | 待测 | Phase 5A 本地 fixture | 同上 |
| 平均 / P95 Loop Steps | 待测 | 待测 | 待补 | 待补 |
| 端到端 P50 / P95 | 待测 | 待测 | 待补 | 待补 |
| Structured LLM fallback 率 | 待测 | 待测 | 待补 | 待补 |

Phase 5A 数字明确标记为本地 fixture，不得替代真实接口/模型的代表性报告；没有代表性
数据时继续保留“待测”，不得用设计门禁或单个演示样例填充生产指标。

### 11.8 当前与完成后的表述边界

现在可以准确表述：

- 完成了 LangGraph Stateful Agent Workflow 的需求分解、技术选型、schema、状态图、
  路由、持久化边界、失败处理、评测和阶段实施设计；
- 完成了阶段 0 LangGraph 技术验证：锁定依赖，运行最小 `StateGraph`，验证
  checkpoint、interrupt/resume、thread 隔离、事件流和图级步数上限，并用架构测试
  约束框架导入边界；
- 建立了 Intent、Slot、Query Understanding、Command、Result、Action、Failure 的
  Pydantic 契约和 Phase 4D V2 OpenAPI；JSON/SSE/readiness 路由可显式注入，默认服务仍不挂载；
- 实现了 Phase-1 Fake Tracking Agent Kernel：规则识别、缺槽 HITL、确定性工具路由、
  结果不变量校验、有限重试、预算终止和 checkpoint replay-safe 执行收据均有离线测试；
  该阶段里程碑定向测试 36 项、当时完整 Python 工作区测试 203 项通过；
- 实现了 Phase-2 五意图规则、Structured Model schema gate、Region Resolver、Slot
  Merger、多意图/切换/更正/控制，以及 SQLite checkpoint、会话幂等、TTL、串行推进和
  State 迁移；该里程碑新增 37 项测试，当时完整 Python workspace 240 项通过；
- 实现了 Phase-3A 时限/资费类型化 Tool、可选 Gateway 装配、路线/重量/时区结果不变量、
  单次 HTTP Client、有界退避、受限 Retry-After 和单进程能力级熔断；新增 22 个 case，
  该里程碑完整 Python workspace 262 项通过；
- 实现了 Phase-4A V2 JSON 能力发现、创建/推进、interrupt 投影、三层幂等、owner
  隔离、外层 timeout 与会话删除；新增 9 个 API case 和 1 个架构 case，当时完整
  Python workspace 272 项通过；
- 实现了 Phase-4B 版本化 SSE、跨传输幂等、建流后脱敏错误、OpenAPI 类型生成、
  Stateful Web、刷新恢复和领域 Renderer；该里程碑完整 Python workspace 277 项、Web
  15 项通过，并完成本地真实浏览器交互验收；
- 实现了 Phase-4C 独立 V2 readiness、低基数 Agent 指标、脱敏停止态 Run Trace 和
  lifespan janitor；新增 6 个 operations/隐私用例，完整 Python workspace 283 项、Web
  15 项通过；
- 实现了 Phase-4D V1 Policy/Device compatibility Adapter、共享结果合同、完整
  Evidence/Provenance 投影和五能力本地 Demo；新增 8 个 Python 与 2 个 Web 用例，完整
  Python workspace 291 项、Web 17 项通过；
- 实现了 Phase-5A V2 多轮黑盒 dataset/client/runner/metrics/reporting、独立响应 schema
  和七项质量门禁；13 场景/17 Turn 本地 fixture 门禁通过，新增 5 个用例后完整 Python
  workspace 296 项通过；
- 实现了 Phase-5B checkpoint 增量语义 Trace、隐私字段白名单、本地故障矩阵和 Agent
  同样本 baseline/experiment 对比；新增 12 个 Trace/对比测试 case，可定位逐 Turn
  regression/improvement，完整 Python workspace `308 passed`、Web `17 passed`；
- 建立 18 场景/21 turn 公开 Query Understanding 回归夹具并全部匹配，但尚不能把该小
  样本结果表述为代表性 Intent Macro-F1 或 Slot F1；
- 现有项目已经具备 RAG grounding、typed ports、只读工具、契约校验和黑盒评测
  基础；
- 已实现从单轮 Dispatcher 渐进复用到受约束 Agent 的兼容路径。

当前不能表述：

- 已配置并评测真实 Structured LLM Provider；当前只有可注入 Port、版本化 Prompt 和
  schema gate；
- 已实现生产级多副本 LangGraph Agent 或跨进程会话锁；当前持久化结论只适用于本地
  单进程 SQLite；
- 已接入轨迹、时限和资费真实接口；
- 已在代表性真实数据、真实接口和 Structured Model 上达到质量门禁；当前只有本地
  fixture baseline；
- 已完成 Redis/分布式熔断、逐 Node OpenTelemetry 耗时 span、真实 Gateway 故障报告或
  Structured Model 代表性对比。

各阶段验收完成后，应同步更新本文第 1、4、6、7、9、10 节，将对应项目从“设计”
迁移到“已实现证据”，并附上代码版本和评测报告。
