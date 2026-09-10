# AI 应用 / Agent 岗位素材：技术复盘

> 用途：整理简历、项目介绍和面试故事，不作为需求或运行手册。
>
> 范围：只记录当前技术 Demo 已实现、可验证的内容。示例数据和功能名称用于验证
> 模块能力，不代表完整真实业务场景；后续场景待需求稳定后另行补充。
>
> 代码基线：`offline-pipeline 0.2.0`、`rag-api 0.5.1`、
> `assistant-api 0.3.6`、`chat-web 0.2.0`、`eval 0.7.0`。
>
> Agent 工程基线：Phase 2 提交 `186208f`；Phase 3A、4A～4D、5A、5B 工作树验证于
> 2026-09-04；真实模型 Adapter、Phase 5C development 对照及 Phase 5D 离线审核／V2 回归完成于 2026-09-07。

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
baseline/experiment 逐 Turn 对比。阶段 2 模型补齐又实现独立 DeepSeek Adapter、单次
有界请求、显式配置和生命周期，并用真实 DeepSeek 验证识别、补槽、规则免模型和超时
安全回退。Phase 5C 进一步分离 Understanding 组件与 Workflow 验收，在 48 条合成
development 样本上量化 Rules/Hybrid 增益及调用成本。代表性语义效果仍待独立 holdout；
Phase 5D 已补跨数据集审核冻结工具和对应 13 场景／28 Turn 的 V2 Mock 集成回归。
Phase 5E 又把“为什么走这条路径”的语义日志与真实 Node wall-clock 分开，通过 OTel
采样/异步导出、全量指标和只读 Grafana Trace 下钻形成可定位的本地观测链路。
默认服务仍未发布 V2，也没有接入真实物流接口。
这个边界需要在面试中主动说明。

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

历史 Phase 5B 基线（提交 `27f735a`）已于 2026-09-06 复核：

- 308 个 Python 测试通过。
- 17 个 Chat Web 测试通过，OpenAPI 生成类型校验和生产构建成功。
- 五能力 HTTP fixture 的 13 场景 / 17 Turn、七项门禁全部通过。

2026-09-07 模型联调收口后新增 34 个 case（33 个模型集成、1 个配置隔离），完整
Python 工作区 `342 passed`，锁文件离线检查通过。真实烟测共 5 次模型尝试：4 次成功、
1 次超时安全回退；三类业务补槽完成，7 次幂等重放无追加模型调用。详见
[模型接入证据](agent-query-model-integration.md)和[真实烟测报告](agent-query-model-live-smoke-20260907.md)。
随后 Phase 5C 新增 38 个组件评测／预算／契约用例，完整 Python `380 passed`。相同
48 条 development 数据上，20 次授权模型调用全部成功，Macro-F1 0.7068→1.0000、
硬槽位 F1 0.9600→1.0000，完整样本通过 34→48，已知用量 40,097 tokens。该数字
不是代表性质量验收，Web 沿用上述未改动基线。见
[组件对照证据](agent-understanding-comparison-20260907.md)。

Phase 5D 随后新增 31 个 case，全量 Python `411 passed`。13 场景／28 Turn 的离线
V2 回归通过，28 次额外幂等重放不追加 Mock 模型调用，三类任务可在重新装配应用后
继续补槽；真实付费请求为 0。审核工具不会自动批准新 holdout，独立人工数据仍待提供。
详见 [Phase 5D](agent-kernel-phase5d.md)。

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
Task Completion、Recovery 和 API Error 门禁。Workflow 评测只调用 HTTP，避免内部函数测试
替代真实调用链。Phase 5B 的报告对比进一步要求 dataset SHA256、完整 Gold 与门禁阈值
全部一致，并把缺失 Turn 和 API error 保留为逐 Turn 回归。

Phase 5C 另以无 Gold 输入／脱敏 observation 文件测量 Understanding 组件，不为评分
给生产 API 增加 debug 字段。实体错值按 FP + FN、缺值按 FN；区分模型正常 unknown、
失败回退、预算 skipped 与未观测行。独立重算和数据指纹防止漏记失败或更换样本制造
虚假提升；小型 development 全通过仍不能证明端到端或未见样本效果。

### 5.6 Demo 的可靠性约束

服务具备 API Key、fail-closed 配置、限流、并发控制、live / ready 分离、结构化
日志、Prometheus、request ID、SSE 生命周期统计、非 root 和只读容器。Web 代理
在服务端注入 Assistant Key，浏览器不持有内部凭据。

## 6. 与 AI 应用 / Agent 岗位的能力映射

| 岗位能力 | 当前项目证据 | 表述边界 |
| --- | --- | --- |
| RAG / Grounding | 混合检索、重排、证据判断、引用和拒答 | 可描述为已实现 |
| Tool abstraction | Agent 五类 Command/Tool；Policy/Device 通过 Port Adapter 复用 V1，三类物流使用 Gateway | 物流能力仍是 Fake Gateway |
| Query Understanding | 五意图 Hybrid、硬实体、schema gate；DeepSeek Adapter 与 Phase 5C Rules/Hybrid 组件对照 | 48 条 development 已测，人工审核 holdout 待验 |
| Routing | V1 显式路由；Agent 由确定性 Policy + Descriptor 白名单路由 | 不允许模型提交任意工具名 |
| Stateful workflow | LangGraph interrupt/resume、SQLite checkpoint、TTL、三层幂等、V2 JSON/SSE、Web 刷新恢复与删除 | 仅本地单进程，默认服务未发布 V2 |
| Failure handling | 分类、有限重试、结果拒绝、受限 Retry-After、能力级熔断；Phase 5B 本地故障矩阵 | 真实接口故障注入仍待完成 |
| Evaluation | Phase 5A/B 多轮 V2 HTTP 与逐 Turn 回归；Phase 5C 组件 F1、成本与失败分母 | fixture／development 不能替代代表性端到端数据 |
| Data governance | Phase 5D 跨文件污染检查、pending 审核、SHA 绑定、排除记录和数据冻结 | 语义近重复需人工审核，身份声明不是认证 |
| Node observability | Phase 5E 实际 Node span、父子采样、全量指标、有界 OTLP 与只读面板 | 无跨服务传播、tail sampling 或生产 SLO 结论 |
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
  结构化日志、非 root 只读容器及 OpenAPI 类型生成；当前基线通过 443 个 Python 测试，
  前端沿用未改动基线的 17 个测试、类型检查和构建证据。
- 接入规则优先的 DeepSeek 语义理解，以版本化 JSON 契约、确定性硬实体重提和
  LangGraph interrupt/resume 完成三类合成查询；真实烟测验证单次预算内失败回退和
  已保存幂等结果免模型重放，不把小样本连通率包装成意图准确率。
- 建立独立 Understanding 组件评测，冻结数据／代码／Prompt 并控制模型调用预算；在
  48 条合成 development 样本上，Rules/Hybrid Macro-F1 为 0.7068/1.0000、硬槽位 F1
  为 0.9600/1.0000，使用 20 次模型调用修正 14 条样本；明确独立 holdout 尚待验证。
- 构建仅依赖 V2 HTTP 的 Agent 多轮 Eval，以 conversation 级顺序执行验证
  interrupt/resume；本地 13 场景/17 Turn fixture 中 Intent、Task Completion 和
  Recovery 均为 1.0000，Wrong Tool/API Error 为 0，明确该数字不是生产准确率。
- 为理解模型建立数据审核冻结与 Workflow 集成回归：源语料／参考集／逐条指纹绑定
  人工审核，拒绝已见污染和过期审核；13 场景／28 Turn 的 Mock 供应商 V2 回归与 28 次
  消息重放通过，验证补槽、确认与恢复，不把 Mock 输出当作模型质量证据。

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
- [Phase 3B-T 邮政轨迹契约与适配器](agent-kernel-phase3b-tracking.md)
- [Phase 3B-T / T2 查询作用域、收据迁移与来源](agent-kernel-phase3b-tracking-t2.md)
- [Phase 3B-T / T3 受控装配、公开来源与 Web](agent-kernel-phase3b-tracking-t3.md)
- [Phase 4A Stateful Agent V2 JSON API 证据](agent-kernel-phase4a.md)
- [Phase 4B Versioned SSE 与 Stateful Agent Web 证据](agent-kernel-phase4b.md)
- [Phase 4C Agent Operations 与隐私安全可观测性证据](agent-kernel-phase4c.md)
- [Phase 4D V1 Tool 复用与五能力 Agent 闭环证据](agent-kernel-phase4d.md)
- [Phase 5A Agent 多轮黑盒评测与质量门禁证据](agent-kernel-phase5a.md)
- [Phase 5B 可靠性故障矩阵、语义 Trace 与 Agent 报告对比证据](agent-kernel-phase5b.md)
- [Phase 5C Understanding 组件评测与预算边界](agent-kernel-phase5c.md)
- [Phase 5D 人工审核冻结与 V2 集成回归](agent-kernel-phase5d.md)
- [Phase 5E 逐节点遥测与本地 Dashboard](agent-kernel-phase5e.md)
- [2026-09-07 同样本 development 对照证据](agent-understanding-comparison-20260907.md)
- `apps/offline-pipeline/src/spb_pipeline/`：采集、解析、OCR、切分、向量化和同步。
- `apps/rag-api/src/spb_rag_api/`：检索、重排、证据判断和回答服务。
- `apps/assistant-api/src/spb_assistant_api/`：显式路由、政策工具、价格匹配和统一协议。
- `eval/src/spb_eval/`：指标、runner、分析和报告。

## 11. LangGraph Agent 化设计与阶段证据

> 状态：总体方案为 In progress；阶段 0～2、3A、4A～4D、5A～5E 的本地切片已完成。当前已有正式
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
Agent，以 LangGraph 作为核心 Workflow Runtime。当前已补齐模型 Adapter、真实烟测、
组件评测工程与 development 对照；Phase 5D 又补审核冻结工具和 V2 Mock 同场景回归。
独立 holdout 仍需真实审核者处理，之后另获预算真实对照；Phase 5E 的逐 Node span /
Dashboard 已完成本地闭环，不把离线完成写成代表性验收。随后取得单份轨迹文档，已完成
阶段 3B-T 的 T0/T1 契约、表单 / 签名与 Gateway，以及 T2 查询作用域、State v3 / 收据
迁移和领域来源的离线切片；T3 又完成受控 V2 装配、公开来源 / Web、语义级熔断及能力
可用性前置检查。T3 本地收尾时 Python `641 passed`、Web `29 passed`；用户确认
接口暂不可达，T4 真实联调暂缓。随后资费文档已完成
[契约评审](agent-kernel-phase3b-postage-analysis.md)及 [P1 领域 / 工作流切片](agent-kernel-phase3b-postage-p1.md)，
P1 新增 86 项后 Python `727 passed`；随后 [P2](agent-kernel-phase3b-postage-p2.md) 新增 160 项，
当时 Python `887 passed`、Web `29 passed`，资费协议已贯通 Mock / Graph / SQLite；
随后 [P3](agent-kernel-phase3b-postage-p3.md) 完成命令绑定确认、公开依据及离线 V2 / Web / Eval，
当时 Python `922 passed`、Web `55 passed`。随后 [6A-1](agent-kernel-phase6a1-browser-identity.md)
完成匿名访客身份隔离，当时 Python `976 passed`、Web `70 passed`。
[6A-2](agent-kernel-phase6a2-sqlite-recovery.md) 又完成受控 SQLite / 整库租约、停服快照与新目录
恢复，新增 44 项后当时 Python `1020 passed`、Web `70 passed`；断网 Docker 三卷重建、
继续和免调用重放通过。真实互通未完成，时限仍待文档。未确认事项记录在[缺口台账](agent-kernel-phase3b-postage-gaps.md)。
[6A-3](agent-kernel-phase6a3-controlled-deployment.md) 又补独立受控入口、冻结镜像、HTTPS /
公共路由和新卷恢复 / V1 回退，新增 21 项后当时 Python `1041 passed`、Web `70 passed`。
[工具链收口](agent-kernel-phase6a3-ci-closeout.md)又完成 Vitest 4.1.11、默认测试隔离与
moderate 门禁；新增 3 项后当前 Python `1044 passed`、Web `70 passed`，完整 npm audit 为 0。
随后修复 Python 基础镜像引用、增加注册表验证，`d28c87c` 两个远程 CI job 成功。
6A-4 已补 33 项内网 HTTP 与 10 项旧主机兼容回归，全量本地 1088 Python / 70 Web；目标更新单独验收。
详细实施基线见
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
硬实体、Structured Model Port/schema gate 和版本化 Prompt。2026-09-07 又实现独立
DeepSeek Adapter、开关和生命周期；Prompt v2 明确模型只补语义分类，编造槽位会被
规则重提结果覆盖。真实烟测已验证语义识别、规则补槽和失败回退。低置信或候选接近时请求澄清，不把模型
自报 confidence 当成真实校准概率。

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
debug event。Phase 5E 随后补齐逐 Node wall-clock span、父子一致采样和本地 Dashboard；
生产跨服务关联与可靠遥测交付仍未验收。

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
baseline 与后续 Structured Model experiment 可以复现。当前已有小量真实烟测记录，
但尚无代表性同样本 Rules/Hybrid 质量对照报告；已看过的烟测输入不能当作未见 holdout。

### 11.3 Agent 能力路线与当前证据

| 岗位能力 | 当前状态 | 下一份关键证据 |
| --- | --- | --- |
| Query Understanding | 五意图 Hybrid；Phase 5C 真实组件对照、Phase 5D V2 Mock 回归 | 人工审核 holdout 与真实 V2 场景联调 |
| Tool / Function Calling | Phase 5A：五查询白名单；本地 Result Wrong Tool 0/11 | 真实五工具 holdout Wrong Tool Rate |
| LangGraph Orchestration | Phase 5B：StateGraph 条件路径、interrupt/resume/checkpoint/Tool retry 脱敏语义 Trace | 逐 Node wall-clock span 与生产采样 |
| Stateful Workflow | Phase 4C：SQLite checkpoint、TTL、三层幂等、owner、删除、API/Web 恢复、janitor | 多副本 checkpointer 与跨进程冲突测试 |
| Agent Loop | Phase 5B：Step/Tool/Retry/recursion 四层预算进入 Trace 与本地超预算矩阵 | 代表性 Loop Step 分布与超预算率 |
| Memory Design | Phase 2：Working State、Metadata、Tool Receipt、RAG 分离 | 数据保留评审和生产清理演练 |
| Failure Handling | Phase 5B：分类、退避、Retry-After、熔断、契约拒绝与本地故障矩阵 | 真实 Gateway 故障注入报告 |
| Human in the Loop | Phase 5A：4 个补槽/多意图场景经 V2 HTTP 恢复 4/4 | 代表性任务完成率 |
| LLMOps / Observability | Phase 5B 语义 Trace；Phase 5E 实际 Node span、采样/OTLP、全量指标与只读 Dashboard | 生产遥测、跨服务传播与 tail sampling |
| Agent Evaluation | Phase 5A/B：V2 门禁；Phase 5C：组件评分；Phase 5D：审核冻结与 28 Turn 集成回归 | 真实模型/接口 holdout |
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
同数据、同 Gold、同门禁约束和逐 Turn 差异。Phase 5B 历史完整 Python workspace
`308 passed`、Web `17 passed`，类型生成检查与 production build 通过；2026-09-07
模型联调与测试配置隔离收口后完整 Python 为 `342 passed`。
Phase 5C 再新增 38 个评测／导出用例后为 `380 passed`，并在离线测试通过后完成
20 次授权模型请求的同样本 development 对照。
Phase 5D 新增 31 个审核／冻结／V2 集成 case 后为 `411 passed`，无新增付费请求。
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
- Result：规则 V2 黑盒 fixture 的 Intent 为 17/17；随后真实 DeepSeek 将三条规则原判
  unknown 的合成改写分别识别为轨迹／时限／资费，均通过规则补槽完成。无关输入一次
  超时、独立复测正确判 unknown；这些小样本不能表述为 Macro-F1、Slot F1 或生产准确率。

#### 故事 E：如何让 Agent 安全失败

- Situation：上游接口和 checkpointer 都可能超时、不可用或返回不兼容数据；
- Task：避免把技术异常解释成无结果，更不能让模型填补事实或在恢复时重复调用工具；
- Action：建立 Failure Taxonomy、有限重试、按工具熔断、结果校验、执行收据、状态迁移
  和 Handoff；
- Result：Phase 3A/4A 分别新增 22/10 个自动化 case，Phase 4B 再增加 SSE/lifespan 与
  Web contract 测试，Phase 4C 增加 6 个 operations/隐私用例，Phase 4D 增加 8 个
  Tool 复用/故障合同用例，Phase 5A 增加 5 个 Eval/门禁用例，Phase 5B 增加 12 个
  Trace/故障/报告对比用例；Phase 5B 历史全量为 Python `308 passed`、Web `17 passed`；
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

#### 故事 I：真实模型联调如何验证成本与失败边界

- Situation：Provider 的 JSON mode 不能保证业务契约，正常输入也可能出现长尾超时；
  配置好 Key 还会让未隔离的本地测试意外读到外部依赖。
- Task：验证模型真的补充语义，同时控制错误路由、重复调用和凭据扩散。
- Action：仅对规则不能决策的输入发一次有界请求，结构校验后重提硬字段；按 Provider
  日志与 Graph 语义 Trace 区分正常 unknown 和失败回退，重复请求复用消息收据；测试
  隔离默认 dotenv／环境，只允许显式配置合成依赖。
- Result：真实 5 次请求中 4 次成功、1 次触发 8 秒 deadline 并安全 handoff；同预算
  人工复测成功，但不删除首次失败、不宣称系统自动重试。7 次已保存结果重放未追加
  模型调用；成功响应已知 7998 tokens，超时账单未知而非零。33 个模型集成用例覆盖
  Mock 合同与 V2 分支，完整 Python 342 项通过；不宣称模型计费 exactly-once。

#### 故事 J：如何证明模型有增益，而不混淆评测对象

- Situation：规则能处理硬实体，却会漏掉口语意图；V2 只暴露业务投影，缺槽提示正确
  不代表实体值提取正确，模型失败回退也可能碰巧得到正确 unknown。
- Task：建立可复算的 Rules/Hybrid 质量／成本对照，同时守住应用边界和付费预算。
- Action：Eval 冻结数据并仅导出无 Gold 输入，Assistant 复用现有 Port 产出独立观测；
  错值计 FP + FN，失败／跳过／缺失行不丢弃，记录正常 unknown 与失败回退。串行
  Model Port 装饰器按尝试数扣预算，Adapter observer 只给脱敏测量，底层 Client
  仍由组合根唯一关闭。用 dataset/input/code/config/prompt 指纹约束同样本对照。
- Result：48 条合成 development 样本、44 条单意图评分行、46 条槽位评分行／26 个
  原子 Gold 值；20 次真实请求带来 14 条改善、0 条退化，Macro-F1 0.7068→1.0000、
  硬槽位 F1 0.9600→1.0000。槽位改善来自正确意图后的规则重提，不是信任模型实体。
  已知用量 40,097 tokens、模型 P95 999.20 ms；新增 38 个离线用例后全量 380 项通过。
- Boundary：所有标签是 draft、4 条已见烟测；不能把 development 全通过写成生产
  准确率。未运行 Graph，因此仍须独立 holdout 和 V2 同场景验收；没有测得货币成本
  或并发 SLA。完整证据见[对照报告](agent-understanding-comparison-20260907.md)。

#### 故事 K：如何防止“组件高分”掩盖工作流问题

- Situation：组件评测的上下文是显式输入，不能证明历史槽位真的被保存；已见 development
  也不能通过改名变成 holdout，人工审阅后再改 Gold 会使审核失效。
- Task：让新数据有审核交接，让已识别的意图进入真实 Workflow 后可验证。
- Action：在独立 Eval 中按参考语料检查 ID／规范化输入／语义 group 污染，绑定候选、
  参考集和逐条 SHA；冻结时重新校验而非信任已编辑 audit。用独立脚本供应商响应经过
  真实 Adapter、V2、LangGraph、SQLite 和 Tool，让 Eval 只看到 HTTP 契约；每个消息
  再用同一幂等键重放，另测应用关闭后重建恢复。原始 query 不改写，编造邮件号被丢弃。
- Result：13 个 development 场景／28 Turn、15 次澄清输入、11 个结果路由及 9 个多轮
  场景全部通过，28 次重放不追加 Mock 模型调用；三类任务在应用重建后仍能继续补槽。
  429／非法结构会安全 handoff，同时让成功路径 Eval 失败；本阶段 31 个新增测试后
  全量 411 项通过，付费模型请求为 0。
- Boundary：Mock 验证集成，不评测模型智力；审核是人类声明而非身份认证，语义近重复
  不由精确哈希检查自动解决。设备旧匹配器的品牌／Pro 候选行为也不等于中文型号精确
  理解。没有自动签字批准 holdout，没有多副本或外部账单 exactly-once 结论。

#### 故事 L：如何测量一个会暂停恢复的 Agent

- Situation：checkpoint 事件能解释分支，却没有真实节点耗时；将跨轮会话当成一个 span
  会把人类等待误计为执行延迟，采样后的 span 数量也不能直接作为业务失败率分母。
- Task：可定位一次实际执行的耗时/重试，又不让遥测泄露业务值或破坏恢复机制。
- Action：在图装配处保留 sync/async 执行形态，用每次 invocation 的独立 root 包含
  Node children；interrupt 立即结束、resume 新开 Trace、retry 每次计数；API receipt
  replay 不进图。组合根拥有 SDK 生命周期，父子一致采样、全量低基数指标、256-span
  有界队列和失败隔离；只记录固定枚举，异常正文/输入/槽位/结果不进入 span。
- Result：新增 32 个测试后全量 `443 passed`；无模型 Docker 烟测 5 次 invocation
  生成 5 条 Workflow Trace / 24 个 Node spans，2 次幂等重放不增加 Trace。Prometheus、
  Tempo 和 10 面板 Grafana 已联通；浏览器验收进一步修正说明裁切、零失败率显示，并
  用只读 Trace 详情代替需要更高权限的 Explore，不提升匿名 Viewer 权限。
- Boundary：这是合成、本地、单实例证据，不是模型质量或生产 SLA。Head sampling 不
  保证捕获所有错误，强杀/队列满可丢 span；没有跨服务 parent、可靠遥测交付或 tail sampling。
  API 时长、Node 时长、模型时长有不同边界，不在简历中混成一个“端到端性能提升”。

#### 故事 M：只有一份不完整接口文档时，如何建立可靠接入边界

- Situation：仅取得一份历史轨迹合同，正文与示例在传输位置、签名、响应接收方和
  操作码上存在冲突；单号查询示例还混入其他单号。直接复制示例会把不确定性变成
  线上事实，也无法证明一次签名实现与供应商兼容。
- Task：先交付可测试的单能力适配器，同时明确哪些部分必须等待接口方确认。
- Action：将不确定选择隔离为显式 provisional profile；JSON 只序列化一次，同一份
  字符串签名后作为表单值传输。对 schema、单号归属、接收方和带时区时间做分层校验，
  拒绝跨单号整批响应，不通过错误文本猜测 no_match / retry，不将冲突操作码擅自统一。
  Gateway 复用类型化 Port 与单次 HTTP，LangGraph 继续拥有重试预算，未确认配置不装配。
- Result：新增 84 个合成合同 / 故障用例，完整 Python 工作区 `527 passed`；覆盖编码、
  原始字节签名、非法字段、同时间冲突、DST、隐私投影、总 deadline、重定向和平台禁用
  MD5。真实业务请求与付费模型请求均为 0，签名向量经本地 OpenSSL 交叉计算。
- Boundary：向量不是供应商确认，MD5 兼容不等于安全认证；该切片未完成真实互通，
  Web 接入在后续 T3 才完成本地验证。
  另在开发前发现会话级参数指纹收据会让新查询返回旧物流状态，因此将“逻辑执行重放”
  与“用户主动重新查询”的作用域拆分列为 T2 前置工作；随后已完成离线修复，见故事 N。
  该案例体现契约治理和失败边界设计，不应表述为“已上线实时物流 Agent”。

#### 故事 N：修正“幂等就是缓存”的混淆，并迁移已有 Agent 状态

- Situation：原 Tool 收据按会话与参数指纹复用，旧测试也把第二次查询不调用 Gateway
  视为成功。这虽避免了历史 checkpoint 重放的重复执行，却会让相同单号的新查询一直
  返回旧轨迹或旧 no_match；更换真实 Gateway 前必须先修正执行语义。
- Task：同时保留同一次执行的恢复能力和新查询的重新取数能力，不靠篡改业务参数刷新，
  不让迁移中的旧结果绕过新版校验，也不把 Mock 的通过写成供应商互通。
- Action：区分 message turn_id、业务 query_id 和逻辑 tool_call_id；查询跨补槽 / 重试
  保持身份，新查询重新生成。收据改以会话与执行 ID 索引，指纹仅校验参数一致性。
  State v3 只兼容可验证的旧 pending invocation；SQLite 事务复制原收据、成功后写迁移
  marker，损坏 / 碰撞整体回滚，TTL 同时清理新旧表。新增类型化观察封装，空结果也携带
  来源、profile、查询时间和历史完整性；写收据、重放和最终响应均校验事实。
- Result：新增 58 个离线用例，完整 Python 工作区 `585 passed`。覆盖新查询取得变化
  轨迹、no_match 后重新取数、历史执行重放零追加 Gateway 调用、旧 SQLite checkpoint
  重建恢复，以及 Graph 已停稳但 API 完成收据写入失败时的限定恢复路径。本轮真实物流
  请求、付费模型请求均为 0。
- Boundary：新鲜度指重新向 Gateway 取数，不代表供应商实时或完整。API 修复仅针对该
  消息的最新持久化停止态，不解决硬崩溃租约或上游已执行、收据未提交的窗口，也不保证
  跨系统 exactly-once。T2 当时只保存领域来源，后续 T3 已补公开投影与 Web，见故事 O。
  该案例适合说明状态建模、幂等边界、兼容迁移和对错误测试预期的修正。

#### 故事 O：让受控 Agent 接入完整路径，同时守住失败与契约边界

- Situation：Postal Adapter 已通过合同测试，但默认应用没有正式 V2 装配，领域来源
  未公开；原 HTTP 熔断会把 200 + 非法业务响应记成成功，未开放能力仍向用户索取槽位。
- Task：贯通配置、鉴权、生命周期、Graph、SQLite、API、Eval 和 Web，不借用无鉴权
  Demo 对外开放，不把没有合同的时限 / 资费偷偷替换成 Fake。
- Action：实现默认关闭的受控工厂，以 `AsyncExitStack` 关闭新资源，V1 Tool 只借用
  不重复启停；可用性检查前移到补槽之前。将一次轨迹尝试的熔断记账放在 Gateway，
  HTTP 传输层在此路径不重复记账，完整 schema / 业务投影成功后才恢复健康。公开来源
  采用白名单 DTO，并同步 OpenAPI、TS 生成、浏览器运行时校验和独立 Eval 镜像；旧来源
  缺失保留 unknown，重放保留原观察时间。
- Result：新增 56 项 Python / 12 项 Web 用例，当前分别 `641 passed` / `29 passed`。
  验证了配置失败关闭、V1/V2 单实例复用、重启补槽、owner / 删除、11 类失败单次记账、
  半开取消恢复和来源隐私；浏览器贯通补槽刷新、轨迹 / 空结果 / 来源展示、新查询变化
  及失败显示。真实物流与付费模型调用均为 0。
- Boundary：这证明本地单进程、Mock 上游的工程闭环，不证明供应商互通、计费 exactly-once
  或生产 SLA。共享代理服务 Key 不能代表多访客身份隔离；实际发布仍要做 T4 合同确认
  和 6A 身份 / 卷 / 备份 / CI / 回退。停止读取响应不等于强制取消已发出的上游请求。

2026-09-09 收尾复核保持 Python `641 passed` / Web `29 passed`，默认与 Agent 模式构建
均通过；本轮仅更新文档，没有新增代码或真实调用。接口不可达后明确将“本地验收完成”
与“T4 暂缓”分开记录，没有把外部依赖问题改写成已接入事实。

初次资费文档评审仅属于**设计讨论素材**；随后 P1/P2 已实现的部分见故事 P/Q。相同的“查询”交互并不意味着
相同协议或业务语义。现有 Graph / Tool Port / 收据 / HTTP 边界可以复用，但新的 CSB
双层签名、必需产品、地域字典和总资费 / 实收 / 增值费口径要在 Adapter 与领域契约中
分别处理。尤其不能把 HTTP 200 当报价成功，或让模型猜产品码和缺失金额。详见
[Phase 3B-P 分析](agent-kernel-phase3b-postage-analysis.md)。

#### 故事 P：识别意图不等于能够执行正确的报价

- Situation：旧资费只收两地和重量，但新文档要求产品，并有标准 / 客户 / 总资费等口径。
  如果恢复期间配置变化，或补槽时非法新重量被忽略，就可能生成条件不一致的报价。
- Action：在原 LangGraph 中注入纯前置策略，以目录验证产品和计费地域；参数指纹生成前
  固定产品、整数克、币种 / 金额口径和目录内容指纹。新报价必须有观察依据，写收据与
  重放校验一致性；旧未完成查询没有核验记录时要求重新发起，旧完成数据保留未知依据。
- Result：86 项新增离线测试，完整 Python `727 passed`，覆盖产品冲突、两后端重放、
  SQLite 重启 / 配置漂移、异常重量、非法报价不落收据和内部绑定不公开；真实请求为 0。
- Boundary：没有真实产品字典、CSB 互通或正式报价 UI；保守范围规则会误拦截否定表达，
  也不能覆盖全部隐含条件。不能把这组合同回归写成生产报价准确率。

#### 故事 Q：合同不完整时，如何推进通路并保留真实边界

- Situation：资费文档存在双层签名、四层错误与多金额口径，但业务样例、字典和单位
  不完整，真实服务也不可访问；只写领域 Fake 无法验证协议映射，自动猜结构又可能错价。
- Action：把未确认内容冻结为具名 synthetic profile，以严格 MockTransport 门禁打通
  map 序列化、业务 / CSB 签名、分层响应、Graph 与 SQLite；profile 语义参与报价上下文
  指纹，旧查询配置变化后必须重新发起。用缺口台账分别管理临时选择与需要接口方确认的证据。
- Failure Handling：只有图拥有两次尝试预算。HTTP / 契约故障由 Gateway 单次记账；
  已识别业务拒绝不重试、不写成功报价收据，但可证明协议路径健康，避免客户资格问题
  熔断整个能力。取消释放半开探测，缺价格 / 计费重不让模型或其他金额字段补齐。
- Result：新增 160 项离线测试，全量 Python `887 passed`；独立 smoke 经补产品、SQLite
  重启、报价、收据重放与新查询，仅发生 2 次 Mock 请求，真实物流 / 模型调用为 0。
  两组本地 OpenSSL 签名向量验证原文与编码顺序，不与运行时 signer 自我循环证明。
- Boundary：本地向量不是供应商 golden vector；CNY / 克 / totalFee 等仍为合成假设，
  不宣称生产报价正确率、CSB 互通或新版资费 UI 已验收。API 产品槽位 / 来源 / Web / Eval
  的缺口留给 P3，不因 Graph 已完成就把公开链路标为完成。

面试时可讲清三个边界：LangGraph 管状态与有限循环，Adapter 管遗留协议和单次健康观察，
Domain / 前置策略管能否执行和报价条件；框架并不能替代合同确认或金额解释。

#### 故事 R：将“收齐字段”与“确认执行”分开，并贯通公开消费者

- Situation：资费已有内部离线 Adapter，但公开 API 不接受产品槽位，UI 也无法说明
  金额口径；用户修改重量后，不能把“确认覆盖”视为接受询价范围。
- Task：在供应商仍不可达的条件下形成可演示、可验证且不误导使用者的 Agent 闭环。
- Action：用纯 Policy 准备命令、生成指纹，复用 LangGraph interrupt / checkpoint
  保存待审条件，resume 后比较当前指纹才执行；计价主体引用变化使旧确认失效。
  为报价定义独立白名单 DTO，协调 API / SSE / OpenAPI / TS / 刷新解析 / Eval，
  不泄露内部计费 ID，不复用轨迹历史完整性解释价格，不擅自补币种或合计费用。
- Result：P3 新增 35 项 Python、26 项 Web 回归，该切片完成时全量 922 / 55；13 场景 / 28 Turn
  公共 V2 Eval 全部通过，8 次 Mock 请求，真实物流 / 模型调用为 0；浏览器验证补产品、
  待确认刷新恢复、报价依据与观察时间保留、业务拒绝无假报价。
- Boundary：这是命令绑定确认和跨消费者合同治理，不是自由 Agent 的自动业务授权，
  也不是生产准确率；“不需要保价”仍可能保守误拒，P4 和代表性理解 holdout 仍未验收。

可追问的核心：审批快照如何绑定到执行参数？配置漂移是否影响暂停会话？为何来源信息
不能直接序列化 Domain？何时应复用收据、何时必须重新查询？这些均有可运行测试支撑。

#### 故事 S：识别“服务 Key 不等于访客”，将身份约束放在 Agent 运行时之前

- Situation：原 V2 已有 owner 检查，但 Nginx 给所有浏览器注入一个服务 Key，导致访客
  实际共享 owner；只验证“不同 API Key 不能互访”不足以证明 Web 多访客隔离。
- Task：在不改 LangGraph 业务图、不引入未完成账号系统的前提下，建立可验收的匿名
  访客边界，并避免刷新或重试把旧请求交给新身份。
- Action：分离服务鉴权/共享配额和 Cookie-derived owner，增加 origin-bound HMAC、
  固定有效期、精确 Origin 与 session_ref 核验，复用 API owner 门禁保护 metadata、
  checkpoint 和幂等。Web 先核验再恢复本地历史；身份改变时中止旧流、不自动重发。
  浏览器测试又发现旧标签页核验响应覆盖 reset Cookie，改为普通核验不签发 Cookie，
  并保留可重复的延迟响应回归。签名轮换接受前代 Token，不延长绝对期限。
- Result：新增 54 Python / 15 Web 测试，全量 976 / 70；两个独立 cookie jar 的 JSON/SSE/
  删除隔离、SQLite 重启、轮换/过期、共享限流与多标签页 UI 通过本地合成验证。没有真实
  模型/物流请求，生产默认未开启。
- Boundary：匿名隔离不是登录、RBAC、计价资格或强制注销；reset 不撤销复制的旧 Token。
  当时备份仍待实施，随后由 6A-2 补齐；6A-3 又补本地 HTTPS / Compose / CI 文件，
  远程 CI、生产部署与分布式会话控制仍待验收，不能称为完整企业 IAM。

面试可追问：为什么不能用 thread_id 授权？为何配额不跟随随机访客 ID？Cookie 重建
与删除会话有什么不同？为什么普通核验不做滑动续期？设计与证据见
[ADR 0017](adr/0017-browser-visitor-identity.md) 和 [6A-1](agent-kernel-phase6a1-browser-identity.md)。

#### 故事 T：Agent 恢复不只恢复图，还要恢复授权、幂等与有效期

- Situation：已有 LangGraph Checkpointer 与 SQLite 持久化，但只备份 checkpoint 会丢失
  owner、创建 / 消息幂等和执行收据。即使整库文件可读，也可能截在一个尚未完成的业务步骤中。
- Task：在真实接口不可达时，为单实例 Agent 建立有明确边界、可复跑的备份与恢复通路，
  不改变 Domain / Graph 规则，不覆盖用户现有数据库，不靠真实请求验证恢复。
- Action：将 0700/0600 路径校验与整库非阻塞进程租约放到基础设施 / composition 边界，
  让运行时和 CLI 协作停服；备份检查未完成 claim，使用 SQLite Backup API 纳入已提交 WAL。
  对 8 张表生成版本化清单和 SHA-256，恢复仅创建新目录，校验后才发布；保留 owner、原始
  TTL、请求 hash、checkpoint 与 Tool 收据。用公开 API 测报价确认继续、跨 owner 拒绝、
  幂等 / 删除 / TTL，再用 UID 10001、断网只读容器和三个命名卷验证跨容器重建。
- Result：新增 44 项存储 / 故障 / API 回归，全量 Python 1020 / Web 70 通过；Docker
  seed / resume / replay 分别为 1 / 1 / 0 次 Fake 工具调用，真实调用为 0。
  完成源库、备份包与恢复库隔离验证，没有把“原库覆盖成功”当作恢复验收。
- Boundary：这是本地单实例 quiescent snapshot，不是在线业务事务快照、任意 crash repair、
  多副本协调或上游 exactly-once。SHA-256 不是签名，0600 不是加密；旧备份可能带回后来
  删除的数据，密钥 / Origin 需独立匹配，异地与保留策略、备份外删除账本仍是待办。

面试可追问：SQLite Backup API 已能在线复制，为什么还要停服？为什么锁文件不能删除？
恢复后什么情况下应该重新执行工具，什么情况下只返回收据？图状态完整为什么仍可能拒绝
恢复？为什么数据库备份不能恢复浏览器身份和删除历史？证据见
[ADR 0018](adr/0018-quiescent-agent-storage-snapshots.md) 和 [6A-2](agent-kernel-phase6a2-sqlite-recovery.md)。

#### 故事 U：部署验收验证信任与恢复，不只是容器 healthy

- Situation：Graph、访客 owner 和 SQLite 恢复已经有单测，但真实代理可能泄露 metrics、
  混淆服务 Key 与访客，或在 Web / API 模式错配时失去恢复能力；供应商暂不可访问。
- Task：不触碰真实配置和旧库，建立独立单实例部署、可执行 CI 和能解释的回退证据，
  同时保持 LangGraph Node 与业务 Tool 的职责不变。
- Action：拆分严格受控入口与纯合成组合根，前者缺依赖 readiness 503、绝不 Fake 兜底；
  后者忽略业务环境、只允许回环 HTTPS。冻结基础镜像 digest / 依赖 lock，绑定 Web 构建
  和代理模式；Key 仅在代理私有 tmpfs，TLS / Host / 方法 / 路由门禁与 API owner 校验分层。
  将存储初始化、运行和停服运维分离，用同一公开 HTTPS 入口串起两访客、整库备份、新卷
  恢复、SSE 继续 / 回放和 V1 → V2 切换。CI 最小权限 / 固定 Actions SHA、不读 secrets；
  演练固定本机 Docker endpoint，防止环境 context 重定向操作目标。
- Result：新增 21 项回归，全量 Python 1041 / Web 70、13 场景 / 28 Turn / 8 次 Mock
  Eval 通过；本地 Docker 四组验收通过。恢复后的完成请求 execute_tool 计数为 0，暂停
  请求继续仅增加 1，再次回放与 V1 返回后不增加。V1 JSON/SSE 可用，未降级 / 删除 Agent DB。
  构建审计同时发现并修复 nanoid high，当时保留 Vitest 2 项 moderate 的后续升级任务。
- Boundary：真实业务调用为 0；CI 文件已实现不代表远程绿灯。回退是同版本 API/UI 模式
  切换，不是旧二进制或 DB schema downgrade；单机自签 HTTPS 不是公网证书 / 企业 IAM，
  也不是多架构、生产 SLA、多副本、供应商 exactly-once 或漏洞全面清零的证据。

调试中的具体教训：短生命周期证书初始化先设置文件 mode 再转移 UID；BusyBox 的正则
重复次数上限与本机工具不同，Key 长度改用 shell 校验；只读 Nginx 要配置所有临时路径；
容器内部 healthy 不能证明 host 发布路径可访问，需要真实 HTTPS 黑盒验证。修复后重跑
整个恢复 / 回退流程，而不是只复查最后一个报错。

后续工具链收口的补充证据：按维护者公告将 Vitest 家族升级至 4.1.11，区分“同一漏洞
影响两个依赖条目”和“两种独立漏洞”；保持运行时依赖与原 70 项业务用例不变。为默认
单测建立不读 dotenv / 不带代理的独立入口，将 CI 安装与审计显式覆盖 dev，收紧至
moderate 门禁，补 3 项合同回归。固定 Node 22 构建内先测试再生成两种 UI，最后再跑公开
HTTPS 恢复 / 回退；全量 Python 1044、Web 70、npm audit 0。最终运行镜像 ID 未变化，
因为升级的测试工具未进入运行镜像；锁文件 / 构建日志与运行产物是不同层次的证据。
该本地结果不覆盖 Python / OS 安全扫描。随后远程 CI 暴露 Python 基础镜像引用问题：
本机缓存中可运行的 image ID 并不一定是注册表可拉取的 manifest digest。通过有界错误
注释定位后，修正官方摘要并增加无本地缓存依赖的注册表校验，不自动跟随可变标签。
`d28c87c` 的两个远程 job 全绿，完成真正干净环境的质量与部署回归。详见
[工具链收口](agent-kernel-phase6a3-ci-closeout.md#6-远程执行揭示的问题与修复)。

后续 6A-4 又区分“代码就绪、依赖连接、实际数据、部署访问条件”四种状态：用户要求保留
内网 HTTP，因此新增显式例外模式，而不是关闭默认 HTTPS 的校验。RFC1918 / 回环精确
origin、独立 Cookie、owner 隔离与路由门禁复用，文档明确 HTTP 不抗链路窃听 / 篡改。
两核心能力继续复用原服务，三项未可用物流不额外补槽、不暴露开发状态；用 33 项回归
覆盖这条交付路径，完整本地 1078 Python / 70 Web 通过。发现原价格表为空时，区分
连接正常与业务数据存在，不用 Fake 让验收“看起来成功”；服务器最终发布证据另补。

另一个真实部署边界是“CI 通过 ≠ 旧宿主机可运行”：旧 Docker 的 seccomp 将 clone3
拒绝为 EPERM，导致 glibc 不回退、aiosqlite 无法启动线程。先排除线程配额和数据库问题，
再用独立入口叠加仅返回 ENOSYS 的拒绝过滤器，保留原过滤器、非 root、只读根文件系统
和 no-new-privileges，而非照搬旧容器的 unconfined。目标原生主机已通过线程 / 存储探针；
10 项新回归验证 ABI、单一 syscall 边界及失败关闭，全量增至 1088。Mac 模拟器不支持
同等 seccomp 检查，因此将完整兼容演练显式交给原生 Linux CI，不把绕过检查算作通过。
这是平台兼容与安全约束之间可解释、可测的窄例外，不代表旧 OS 已获得长期安全支持。

面试可追问：为何正常入口不能自动注入 Fake？为什么 TLS 之外还要检查 Host / Origin？
代理为什么不能自动 retry Agent POST？恢复库后为什么还要保留密钥和原幂等请求？API/UI
回退和 schema 回滚有何区别？如何证明 CI 不误用本地凭据？证据见
[ADR 0019](adr/0019-controlled-deployment-and-offline-ci.md)、
[6A-3](agent-kernel-phase6a3-controlled-deployment.md)和[可复跑脚本](../deploy/agent/smoke.py)。

### 11.6 简历 bullet 模板

当前可以使用、但必须明确 `Phase 3B-T T3 / Phase 3B-P P1–P3 / Phase 5E / Phase 6A-1–3 / Fake 或 Mock Gateway / fixture 或 development / local SQLite / opt-in V2`
范围的工程表述：

- 为 LangGraph Agent 构建独立受控部署与离线 CI 工作流，分层实现 HTTPS / 代理身份 /
  路由白名单和单实例持久卷，新增 21 项回归；通过真实 Nginx / Docker 合成演练验证双
  访客隔离、新卷恢复、SSE 幂等回放与 V1 回退，并通过两个远程 GitHub CI job（真实业务发布另验）。
- 将 Agent Web 测试入口与真实运行配置分离，完成 Vitest 安全大版本迁移及全开发依赖
  moderate 门禁；保持原 70 项用例 / 运行时依赖不变，验证 Node 22 构建内测试与两种
  UI 构建，npm 已知漏洞报告归零（限定当时锁文件 / npm 公告库，非整体安全认证）。
- 为 LangGraph Agent 实现停服整库备份与独立目录恢复，覆盖 owner、checkpoint、消息幂等
  和 Tool 收据；以合作进程租约、版本 / 摘要校验及 44 项新增回归验证恢复后继续、免调用
  重放、TTL 与删除，并完成断网 Docker 三卷演练（本地单实例，非分布式灾备）。
- 为 Stateful Agent 分离代理服务鉴权与匿名访客归属，在 API 边界实现签名 Cookie、
  同源校验和身份绑定的会话恢复；修复多标签页 Cookie 覆盖竞态，新增 54 后端 / 15 Web
  回归，完成双访客越权、幂等/重启与浏览器验证（本地 opt-in Demo，非完整登录系统）。
- 基于 LangGraph interrupt / checkpoint 构建命令绑定的资费确认流程，区分补槽、覆盖冲突
  与执行确认；通过白名单报价依据贯通 V2 JSON/SSE、Web 刷新恢复和独立 Eval，新增
  35 项 Python / 26 项 Web 回归，并完成 13 场景 / 28 Turn 合成黑盒验证（非真实资费接入）。
- 在资费合同不完整、服务不可达的条件下，以版本化临时 profile 实现独立协议适配层，
  完成双层签名、四层成功校验、单次语义熔断与 LangGraph / SQLite 离线通路；新增
  160 项合同 / 故障 / 恢复测试，并以缺口台账区分工程完成和供应商互通，避免未知金额兜底。
- 为资费 Agent 建立产品 / 地域可执行门禁、精确整数克与显式定价上下文，在生成执行
  参数指纹前固定报价口径；通过恢复时的目录内容校验、类型化报价观察和收据校验防止
  条件漂移，新增 86 项合成领域 / 多轮回归测试（尚未完成供应商互通）。
- 贯通受控 V2 Agent 装配、SQLite、多轮 Web 与独立 Eval 契约，以白名单来源 DTO
  展示查询时间和历史完整性；将单次语义熔断与 Graph 重试预算分离，覆盖 11 类失败
  单次记账及半开取消恢复，新增 56 项 Python 与 12 项 Web 回归测试，保持真实接口默认关闭。
- 为 LangGraph 查询 Agent 拆分消息幂等、业务查询与 Tool 执行身份，以查询内执行收据
  支持 checkpoint 重放，并修复同会话相同参数新查询误用旧结果；实现 State v3 与
  SQLite 原子收据迁移、类型化来源观察和恢复结果校验，新增 58 个离线回归用例。
- 基于历史邮政接口设计 provisional Gateway profile，以严格 wire schema、单次 JSON
  签名 / 表单编码、跨单号整批拒绝和 UTC 时间线隔离遗留协议；新增 84 个离线合同 / 故障
  测试，将供应商互通与本地实现验证分开验收，保留默认关闭和无 Fake 回退边界。
- 为 LangGraph Agent 设计实际 Node OpenTelemetry span、父子一致采样与全量 Prometheus
  指标，区分 interrupt/resume、实际重试与幂等重放；通过白名单字段、有界异步导出与
  lifespan 管理隔离隐私和监控故障，完成本地 Tempo/Grafana 只读 Trace 定位闭环。

- 基于 LangGraph `StateGraph` 实现可注入依赖的轨迹查询 Agent Kernel，以显式 Node、
  条件边和 `interrupt/resume` 编排补槽、执行、校验、恢复与响应；使用业务 Step、逻辑
  Tool Call、Retry 和 recursion limit 四层预算保证确定终止。
- 设计类型化 `Command -> Registry -> Tool -> Result Validator` 执行链，并以
  `(conversation_id, tool_call_id)` 执行收据处理查询内 checkpoint 重放，以参数指纹
  校验一致性；离线分支重放不追加 Gateway 调用，而新的 query_id 重新取数。
- 实现五意图 Hybrid Query Understanding，以确定性邮件号、Decimal 重量和可注入行政区
  Resolver 处理硬实体，以 Slot Merger 保护跨轮冲突，并用版本化 Structured Model
  schema gate 阻止任意工具名；18 场景/21 turn 公开回归夹具全部匹配，明确该结果不是
  生产 Macro-F1。
- 为 Hybrid 接入独立 DeepSeek Adapter，以单次 deadline、输出校验和规则回退限制模型
  权限与成本；通过真实合成烟测验证语义识别、三类补槽及超时安全退出，7 次幂等重放
  无新增模型调用，质量提升留待冻结 holdout 后量化。
- 构建 `AsyncSqliteSaver` + Metadata/API Idempotency/Tool Receipt 分层持久化，在关闭连接
  并重新编译 Graph 后恢复同一 interrupt；测试同时覆盖重复 resume、同会话并发拒绝、
  30 分钟可配置 TTL 清理和 v1/v2 -> v3 State migration。
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
- 设计 Understanding 组件与 V2 Workflow 分层评测，采用无 Gold 请求、独立观测契约、
  版本／数据指纹和有界模型调用；在 48 条合成 development 数据上将 Macro-F1 从
  0.7068 提升至 1.0000，20 次真实请求、14 条改善／0 条退化，同时保留用量和尾延迟
  代价；人工审核 holdout 与完整 V2 场景另行验收。
- 实现数据审核冻结与 Understanding→Workflow 分层回归，以跨文件污染校验、逐条 SHA、
  人工批准／排除记录防止无意泄漏；13 场景／28 Turn 的 V2 Mock 集成回归及 28 次幂等
  消息重放通过，验证多轮补槽、切换／覆盖确认和应用重建恢复，明确不代表生产模型质量。

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
| Intent Macro-F1 | 0.7068 | 1.0000 | Phase 5C，48 条 development／44 条单意图评分 | [对照证据](agent-understanding-comparison-20260907.md) |
| 联合硬槽位 micro-F1 | 0.9600 | 1.0000 | Phase 5C，46 行／26 个原子 Gold 值 | 同上 |
| 代表性 Intent / Slot F1 | 待测 | 待测 | 独立审核 holdout 待补 | 待补 |
| Intent Accuracy | 1.0000（17/17） | 待测 | Phase 5A 本地 fixture | `eval/baselines/phase5a-local-fixture-v1` |
| Required Input Accuracy | 1.0000（4/4） | 待测 | Phase 5A 本地 fixture | 同上 |
| Wrong Tool Rate | 0.0000（0/11） | 待测 | Phase 5A 本地 fixture | 同上 |
| Task Completion Rate | 1.0000（10/10） | 待测 | Phase 5A 本地 fixture | 同上 |
| 不必要澄清率 | 0.0000（0/17） | 待测 | Phase 5A 本地 fixture | 同上 |
| Recovery Rate | 1.0000（4/4） | 待测 | Phase 5A 本地 fixture | 同上 |
| 平均 / P95 Loop Steps | 待测 | 待测 | 待补 | 待补 |
| 端到端 P50 / P95 | 待测 | 待测 | 待补 | 待补 |
| Structured LLM 调用率 | 0/48 | 20/48（41.67%） | Phase 5C development 组件 | [对照证据](agent-understanding-comparison-20260907.md) |
| 模型失败 / 实际尝试 | N/A | 0/20 | 同上；不含先前烟测 | 同上 |
| 模型 P50 / P95 | N/A | 725.34 / 999.20 ms | 同上；非 V2 端到端 | 同上 |
| 已知 total tokens | 0 | 40,097 | 同上；未知用量调用 0，非货币账单 | 同上 |
| V2 语义工作流场景／Turn 通过 | — | 13/13、28/28 | Phase 5D development／Mock Provider | [Phase 5D](agent-kernel-phase5d.md) |
| V2 消息重放追加模型调用 | — | 0/28 次重放 | 同上；不是供应商计费保证 | 同上 |

Phase 5A 数字是本地 fixture，Phase 5C 是 synthetic development，均不得替代代表性报告；没有代表性
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
- 实现 DeepSeek Query Understanding Adapter，复用共享 HTTP 并把并发排队和网络纳入
  总 deadline；通过 JSON/schema 两层检查、规则硬实体重提和失败回退保护 Tool 边界。
  Mock HTTP + V2 验证“模型识别 -> 缺槽 interrupt -> 规则 resume -> 幂等完成”，
  明确未将模型计费 exactly-once 或真实模型准确率列为已验证能力。
- 完成真实 DeepSeek 小量烟测，5 次尝试含 4 次成功与 1 次超时安全回退；保留失败与
  未知用量，三类业务补槽和 7 次免模型幂等重放已验证；模型集成 33 个 case、测试
  配置隔离 1 个 case，完整 Python workspace `342 passed`。
- 完成 Phase 5C 独立 Understanding 组件评测与 48 条 development 同样本对照，真实
  模型使用本轮授权的全部 20 次请求、无重试，14 条改善／0 条退化；指标、用量、版本
  指纹与边界已落盘，新增 38 个用例后完整 Python workspace `380 passed`。
- 完成 Phase 5D pending 审核、跨文件污染校验和冻结工具，以及 13 场景／28 Turn
  V2 Mock 集成回归、28 次重放和三类应用重建恢复；新增 31 个用例后全量 `411 passed`。
  未自动批准真实 holdout，未追加付费调用。

- 完成 Phase 5E 逐 Node wall-clock / OTel / Dashboard：新增 32 个测试后 `443 passed`，
  Docker 合成烟测、PromQL 校验与浏览器验收通过；没有变更生产默认 V1，也没有付费调用。
- 完成 Phase 3B-T 的 T0/T1：轨迹 provisional 契约、表单传输、兼容签名、严格解析及
  Gateway 领域投影；新增 84 个用例后当时全量 `527 passed`，真实请求为 0。
- 完成 Phase 3B-T / T2：查询作用域与执行收据、State v3 / SQLite 迁移、来源观察封装、
  结果校验及 Mock V2 集成；再新增 58 个用例后当时全量 `585 passed`。
- 完成 Phase 3B-T / T3：受控组合根、公开来源 / Web、语义级熔断和未开放能力前置阻断；
  新增 56 个 Python / 12 个 Web 用例，全量分别 `641 passed` / `29 passed`。完成本地
  浏览器验证；实际环境未开启，没有真实物流或付费模型请求，T4 仍待确认与授权。
- 完成 Phase 3B-P / P1：资费目录门禁、产品补槽、整数克、报价观察与上下文恢复保护；
  86 项新增测试后当时全量 `727 passed`，Web `29 passed`。
- 完成 Phase 3B-P / P2：资费双层签名 / 四层校验 / profile 绑定 / 语义熔断、Mock 至
  Graph / SQLite / 执行收据通路；160 项新增测试后全量 `887 passed`、Web `29 passed`，
  并提供可复跑合成烟测与缺口台账；当时尚未实现 P3。
- 完成 Phase 3B-P / P3：命令绑定确认、非敏感计价身份引用、公开报价依据、独立离线组合根，
  新增 35 Python / 26 Web 后全量 `922 passed` / `55 passed`；13 场景 / 28 Turn Eval 与
  本地浏览器验证通过，无真实物流 / 模型调用。当时 P4、完整否定理解、6A 和代表性 holdout 未完成。
- 完成 Phase 6A-1：分离代理服务 Key/共享配额与匿名访客 owner、签名 Cookie/同源校验、
  核验后恢复与多标签页竞态保护；新增 54 Python / 15 Web 后全量 `976 passed` / `70 passed`。
  本地浏览器/类型/隔离构建通过，真实调用为 0；当时 6A-2/6A-3、登录与完整生产部署未完成。
- 完成 Phase 6A-2：受控目录 / 整库进程租约、含 WAL 的停服快照、新目录恢复及版本 /
  摘要 / 结构检查；44 项新增测试后全量 `1020 passed` / Web `70 passed`，类型与隔离构建通过。
  Docker 命名卷使用独立合成项目，UID 10001、无网络 / dotenv，恢复继续 1 次 Fake 调用、
  再次重放 0 次；无运行容器，保留三个合成卷。镜像平台元数据限制留给 6A-3，真实调用为 0。
- 完成 Phase 6A-3 本地 / 合成验收：独立受控入口 / Compose / 冻结镜像、HTTPS 公共
  边界、实际 CI 工作流文件；新增 21 项后全量 `1041 passed` / Web `70 passed`，类型 /
  双构建与 13 场景 / 28 Turn Eval 通过。Docker 新卷恢复、SSE 继续 / 回放和 V1 返回 V2
  全流程通过，保留合成卷、移除本次容器 / 网络，真实调用为 0；远程 CI 尚未运行。
  当时 npm runtime audit 为 0，Vitest 2 项 moderate 待升级，未声称所有依赖安全合格。
- 完成 6A-3 工具链收口：Vitest / mocker 4.1.11，完整 npm audit 为 0；默认单测不读
  dotenv / 开发代理，CI 包含 dev / 拦截 moderate / 严格 Node 合同，Docker 构建先执行
  测试。新增 3 项合同回归后全量 `1044 passed` / Web `70 passed`，Node 22 双构建及
  HTTPS 新卷恢复 / SSE / V1 回退重跑通过；当时无真实业务调用，远程 CI 尚待授权后验证。
- 完成 6A-3 远程收尾：从失败注释定位 Python 镜像引用错误，增加官方注册表 manifest
  校验；`d28c87c` 两个 GitHub job 成功，质量门禁、双 Web 构建与恢复 / 回退通过。
- 进入 6A-4 内网单实例发布：显式 HTTP 例外不更改默认 HTTPS，复用原 RAG / MySQL，
  三项物流正常 unavailable；33 项新增回归，本地 1078 Python / 70 Web 通过，目标验收另补。

当前不能表述：

- 已完成代表性 Structured LLM 质量评测或生产 SLA 验证；当前完成工程接入、Mock、
  真实烟测和 development 对照，未审核小样本不能替代独立 holdout 或生产长尾统计；
- 已完成分支保护、自动镜像发布、公网 HTTPS / 秘密托管或所有依赖安全审计；
  已有两个 GitHub CI job 成功，但不能推断这些独立发布治理事项已经完成；
- 已实现生产级多副本 LangGraph Agent 或允许多个进程协调推进会话；6A-2 的整库合作
  进程租约拒绝第二个实例，不是分布式会话锁，持久化结论仍只适用于本地单实例 SQLite；
- 已实现加密异地灾备、任意 crash repair、备份外删除账本或数据库 RTO / RPO SLA；当前只有
  停服 / 合成夹具恢复验证，没有把快照大小或一次运行耗时转写成生产指标；
- 已接入轨迹、时限和资费真实接口；
- 已通过供应商签名互通、保证供应方实时性或实现跨系统 exactly-once；当前查询内
  重放与新查询重新取数仅有本地 Fake / Mock 回归证据；
- 已在代表性真实数据、真实接口和 Structured Model 上达到质量门禁；当前只有本地
  fixture 与 development 证据；
- 已完成 Redis/分布式熔断、生产跨服务 OpenTelemetry、真实 Gateway 故障报告或
  Structured Model 代表性对比。

各阶段验收完成后，应同步更新本文第 1、4、6、7、9、10 节，将对应项目从“设计”
迁移到“已实现证据”，并附上代码版本和评测报告。
