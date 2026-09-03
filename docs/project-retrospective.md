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
- Assistant 只支持显式模式、单轮、单工具调用，没有服务端记忆、自动意图识别、
  自主规划或 Agent Loop。
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
- `apps/offline-pipeline/src/spb_pipeline/`：采集、解析、OCR、切分、向量化和同步。
- `apps/rag-api/src/spb_rag_api/`：检索、重排、证据判断和回答服务。
- `apps/assistant-api/src/spb_assistant_api/`：显式路由、政策工具、价格匹配和统一协议。
- `eval/src/spb_eval/`：指标、runner、分析和报告。

## 11. 下一阶段 Agent 化设计素材

> 状态：本节记录已完成设计、但尚未由代码和评测证明的下一阶段方案。实施前不能把
> 它写成简历中的“已实现能力”。完成对应验收后，再把占位项替换成真实代码、报告和
> 指标。

下一阶段计划把当前单轮 Dispatcher 演进为受约束、可观测、可恢复的 Stateful Tool
Agent，并接入邮件轨迹、寄递时限和资费三个只读查询能力。详细实施基线见
[Stateful Agent Workflow 实施方案](agent-workflow-implementation-plan.md)。

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
执行约束在类型化状态机和白名单中。

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

#### 决策二：Hybrid Query Understanding

采用以下优先级：

```text
显式 UI 意图
  > 当前 Workflow 上下文
  > 确定性实体提取和规则
  > Structured LLM fallback
```

邮件号、重量和行政区划等硬字段不依赖模型自由生成；模型只在规则不能可靠判断时
输出受 Pydantic / JSON Schema 约束的候选意图和槽位。低置信或候选接近时请求澄清，
不把模型自报 confidence 当成真实校准概率。

#### 决策三：Query Understanding 与 Routing 分离

Query Understanding 可以输出 `IntentCandidate`、槽位、缺失字段和歧义，但不能返回
任意可执行函数。Deterministic Router 根据服务端 `ToolDescriptor` 把已验证 Intent
映射到固定工具。这样可以分别评测“理解是否正确”和“系统是否调用正确工具”，也能
阻止 Prompt Injection 直接越权调用工具。

#### 决策四：类型化 Command 和 Result

现有 `execute(question: str)` 适合单轮问题，但不适合轨迹、时限和资费。下一阶段用
`TrackingCommand`、`DeliveryTimeCommand`、`PostageCommand` 等判别联合承载参数，
并为轨迹时间线、时限估计和资费报价建立独立结果类型。

类型化边界带来三项收益：

- Tool Executor 不再重复解析自由文本；
- 外部 Adapter 字段变化不会直接泄漏到 API 和 Web；
- Eval 可以对参数、路由和结果不变量进行精确断言。

#### 决策五：显式 State、Event 和 Checkpoint

Agent State 只保存当前意图、已确认槽位、缺失字段、Workflow Phase、工具记录、预算
和错误摘要。状态通过 Event 和纯 Reducer 更新，并在关键转换后 checkpoint。

Store 通过 Protocol 隔离：单测使用内存实现，本地 Demo 使用 SQLite，多副本场景再
接 Redis。`revision` 乐观锁和 `Idempotency-Key` 用来防止同一会话并发覆盖及重复
工具调用；TTL 和主动 reset 控制数据保留。

不保存模型思维链，也不把状态存储包装成长期用户记忆。Working Memory、RAG
Knowledge 和 Long-term User Memory 在设计中被明确区分。

#### 决策六：Failure 是状态机的一部分

错误不统一包装成“没有查询到”：

| 类别 | 设计行为 |
| --- | --- |
| 缺少字段 / 意图歧义 | 暂停并等待用户补充 |
| 输入非法 | 保留有效状态，指出具体字段 |
| 无业务结果 | 正常终态，不生成推测事实 |
| 超时 / 限流 / 暂时不可用 | 对只读调用有限重试或熔断 |
| 上游契约错误 | 阻止展示，不重试错误数据 |
| 状态冲突 | 重新加载或要求安全重试 |
| Loop 超预算 | 强制终止并保留 Trace |

只对明确幂等、可恢复的错误重试。轨迹、资费等上游不可用时，模型不得生成近似事实；
降级必须来自另一个已注册且可验证的数据源。

#### 决策七：不记录 Chain of Thought，用结构化 Trace 解释行为

每次请求记录 Query Understanding 来源、候选意图、公开 signals、Workflow 转换、
工具名、尝试次数、校验结果和 finish reason。这样可以定位失败步骤，同时避免记录
模型内部推理、完整邮件号、凭据或高基数参数。

### 11.3 计划形成的 Agent 能力证据

| 岗位能力 | 计划实现 | 完成后应提供的证据 |
| --- | --- | --- |
| Query Understanding | 规则、上下文与 Structured LLM 混合解析 | Intent/Slot 数据集、Macro-F1、错误分析 |
| Tool / Function Calling | Tool Descriptor、类型化 Command、白名单执行 | Wrong Tool Rate、未授权调用测试 |
| Stateful Workflow | State、Event、Reducer、Checkpoint、TTL | 多轮测试、重启恢复、状态 Trace |
| Agent Loop | 有限 Step、调用和重试预算 | Loop Step 分布、超预算率和终止测试 |
| Memory Design | Working Memory 与知识检索分离 | 状态 schema、数据保留与脱敏说明 |
| Failure Handling | 重试、熔断、契约校验和 Handoff | Failure Injection 和恢复率报告 |
| Human in the Loop | 歧义、越界和预算耗尽时转人工语义 | Handoff 场景测试 |
| LLMOps / Observability | Prompt/Parser 版本、Trace、指标 | 可复现 Trace 和 Dashboard 定义 |
| Agent Evaluation | 多轮黑盒场景与 baseline/experiment | Completion、Clarification、Recovery 报告 |
| Grounding | 复用现有 RAG 引用与拒答 | 引用和事实覆盖回归 |

### 11.4 推荐演示路径

最终面试 Demo 应覆盖正常路径和异常路径：

1. 输入完整邮件号，规则识别后直接查询轨迹；
2. 询问寄递时限但缺少寄达地，Workflow 暂停并补槽；
3. 输入“这个要多久”，候选意图不足，Agent 主动澄清；
4. 补槽过程中切换到资费查询，系统确认重置而不混用旧状态；
5. 上游第一次超时、第二次成功，Trace 展示有限重试；
6. 上游返回 HTTP 200 但缺少必要字段，Validator 阻止不可信结果；
7. 重复提交同一消息，幂等机制避免重复 Tool Call；
8. Prompt Injection 要求调用未注册工具，Router 拒绝执行。

只展示 Happy Path 很难体现 Agent 工程能力；至少一半演示应覆盖歧义、恢复、契约错误
和安全边界。

### 11.5 面试故事模板

#### 故事 A：为什么没有使用自由 ReAct

- Situation：业务能力固定，但输入自然、字段可能跨轮补充；
- Task：既体现 Agent 能力，又保证不会调错工具或无限循环；
- Action：把不确定性放入 Query Understanding，执行由 typed state machine、工具
  白名单和预算控制；
- Result：完成后填写 Wrong Tool Rate、Loop 超预算率、Task Completion Rate 和故障
  恢复测试结果。

#### 故事 B：如何处理 Stateful Workflow

- Situation：HTTP 请求天然无状态，但补槽流程跨多轮且可能并发、重试或服务重启；
- Task：保证流程可恢复且不会重复调用工具；
- Action：使用 Event + Reducer、checkpoint、revision 乐观锁和 Idempotency-Key；
- Result：完成后填写重启恢复、重复请求、并发冲突和 TTL 清理的测试证据。

#### 故事 C：如何平衡规则和 LLM

- Situation：纯规则难覆盖自然表达，纯 LLM 又可能误识别硬字段；
- Task：提高理解覆盖率，同时保持可解释和可回归；
- Action：显式选择和状态优先，正则/词典处理硬实体，Structured LLM 只作为
  fallback，低置信时澄清；
- Result：完成后填写规则命中率、模型 fallback 率、Intent Macro-F1、Slot F1 和
  不必要澄清率。

#### 故事 D：如何让 Agent 安全失败

- Situation：上游接口可能超时、限流、返回空数据或返回错误 schema；
- Task：避免把技术异常解释成无结果，更不能让模型填补事实；
- Action：建立 Failure Taxonomy、有限重试、按工具熔断、结果校验和 Handoff；
- Result：完成后填写错误恢复率、契约错误拦截数和故障注入用例通过率。

### 11.6 简历 bullet 模板

以下 bullet 只有在对应代码、测试和报告完成后才能使用；方括号内容必须替换为真实
数字：

- 设计并实现受约束 Stateful Tool Agent，以 Pydantic Structured Output 完成五类
  Query Understanding，通过确定性 Tool Registry、类型化 Command 和 bounded
  Agent Loop 控制工具执行，在 `[N]` 条评测集上取得 Intent Macro-F1 `[X]`、Slot
  F1 `[Y]`，明确意图 Wrong Tool Rate 为 `[Z]`。
- 构建 Event + Reducer + Checkpoint 的多轮工作流，加入 revision 乐观锁、
  Idempotency-Key、TTL 和重启恢复；在重复提交、并发更新和中断恢复测试中实现
  `[结果]`，避免重复只读 Tool Call。
- 将政策 RAG、设备价格、邮件轨迹、寄递时限和资费封装为类型化只读工具，引入
  schema validation、有限重试、按能力熔断和错误分类，在 `[N]` 个故障注入场景中
  达到恢复率 `[X]`，并保持无业务结果与技术失败语义分离。
- 建立 Agent 多轮黑盒评测和可观测链路，覆盖 Task Completion、Clarification、
  Routing、Recovery、Loop Steps、P95 延迟及 Prompt/Parser 版本对比，通过脱敏
  Trace 定位每个 Workflow Step。

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

- 完成了 Stateful Agent Workflow 的需求分解、schema、状态机、路由、失败处理、
  评测和阶段实施设计；
- 现有项目已经具备 RAG grounding、typed ports、只读工具、契约校验和黑盒评测
  基础；
- 已确定从单轮 Dispatcher 演进到受约束 Agent 的兼容路径。

当前不能表述：

- 已实现自动意图识别、服务端记忆或 Agent Loop；
- 已接入轨迹、时限和资费真实接口；
- 已达到实施方案中的质量门禁；
- 已完成 Redis、熔断、Agent Trace 或多轮评测。

各阶段验收完成后，应同步更新本文第 1、4、6、7、9、10 节，将对应项目从“设计”
迁移到“已实现证据”，并附上代码版本和评测报告。
