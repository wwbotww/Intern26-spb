# AI 应用 / Agent 技术复盘与面试素材

用途：项目介绍、技术追问和简历取材，不是状态页或部署手册。
以下只讲已实现设计；数字与验证边界集中于[交付摘要](history/agent-delivery-summary.md)，
当前能力/未完成项见[状态页](current-status.md)。

## 1. 30 秒项目介绍

我实现了一个基于 LangGraph 的只读查询 Agent，将政策 RAG 和结构化价格工具接到统一会话入口。
规则优先、模型补充语义，再由确定性策略完成澄清、补槽、冲突确认和工具执行。
系统支持 SQLite 恢复、幂等重放、版本化 SSE、访客隔离，并用独立 Eval、故障测试和
逐节点观测验证行为。已完成单实例内网交付；物流 Adapter 有离线通路，真实互通和
代表性模型质量仍分开验收。

## 2. 最有价值的设计故事

每个故事按“问题 → 决策 → 验证 → 限制”展开，不背阶段编号。

### A. 不把模型理解等同于执行权限

问题：纯关键词覆盖不了口语，完全信任模型又可能编造单号或选错工具。
做法：定义版本化 Understanding schema；显式入口/活动会话/规则优先，模型只补语义，
硬实体从用户原文重新提取。Policy 只对明确意图、可用能力、完整 Command 放行。
验证：模型编造槽位被丢弃、未知/多意图澄清、规则免调用、超时回退及 V2 多轮测试。
限制：score 不是校准概率；目前否定/地区覆盖仍有限，不能用 development 满分证明泛化。
依据：ADR [0003](adr/0003-hybrid-query-understanding.md)、[0006](adr/0006-typed-tool-routing.md)。

### B. LangGraph 负责运行时，业务规则不绑定框架

问题：把校验、HTTP、SQL、鉴权都写在 Node 内，会难测试、难复用，也难替换供应商。
做法：Domain/Port → Service/纯 Policy → Graph/Runtime → Adapter/组合根分层；
LangGraph 只承担条件边、interrupt/resume、checkpoint 与执行调度。
验证：架构 import 约束、纯策略测试、不同 Gateway/持久化替身与实际 Graph 集成。
限制：可替换边界不是零成本迁移；生产 checkpointer/分布式协调仍要单独验证。
依据：[架构](workspace-architecture.md)、ADR [0002](adr/0002-langgraph-workflow-runtime.md)。

### C. 幂等不是业务缓存，恢复也不只恢复 Graph

问题：按“会话+参数”缓存收据会让用户再次查同一单号得到旧轨迹；只备份 checkpoint
会遗漏 owner、TTL、消息 claim 和结果收据。
做法：区分 conversation/turn/query/tool_call 身份，参数指纹只校验完整性；
创建、消息、执行三层幂等；新查询重新取数。停服整库备份并只恢复到新目录。
验证：新查询刷新、历史重放、状态/收据迁移、进程重建、8 表恢复、越权和 TTL 回归。
限制：上游执行到本地提交仍有 crash 窗口，不宣称计费 exactly-once、热备或多副本。
依据：ADR [0012](adr/0012-query-scoped-tool-receipts.md)、[0018](adr/0018-quiescent-agent-storage-snapshots.md)。

### D. 不完整协议也能推进，但不能虚构“已互通”

问题：只有接口文档，缺网络、签名向量、响应层级及金额单位。
做法：领域与 wire 分离，具名 provisional profile 固定假设；
一次序列化、两层签名、分层错误、完整语义校验后才记熔断成功。
不明确处列缺口，资费 transport 强制 Mock，正式能力不可用时不加载 Fake。
验证：独立复算本地签名字节、错误接收方/流水/单号、空结果/业务拒绝、
超时/取消/半开位释放、API/Web/Eval。
限制：本地签名向量不是供应商 golden vector，HTTP 200 不代表有效业务结果。
依据：[轨迹](../apps/assistant-api/docs/integrations/tracking.md)、[资费](../apps/assistant-api/docs/integrations/postage.md)。

### E. “确认”必须绑定实际执行条件

问题：收齐两地和重量不等于能报价，永久 approved=true 可能被后续条件变更错误复用。
做法：产品、供应商地区、精确整数克、计价身份/口径/profile 先冻结；
用户独立确认 Command 指纹，变更条件或配置必须重新确认/重新查询。
验证：提前夹带确认、改重量同时确认、配置/身份漂移、重启与旧快照。
限制：当前仅国内实重无增值服务合成试算；不能猜币种、补零或自动累计含费未知的费用。
依据：ADR [0014](adr/0014-explicit-postage-pricing-context.md)、[0016](adr/0016-command-bound-postage-review.md)。

### F. 复用旧业务，而不是另写一套 Agent Tool

问题：Agent 化容易复制 RAG/价格逻辑，形成两套校验与生命周期。
做法：Compatibility Adapter 只翻译 Command/Result，V1/V2 借用同一 Tool，
共享结果合同，分别公开投影；lifespan 唯一拥有 Client/连接池。
验证：V1/V2 同实例、初始化/关闭仅一次、完整引用/价格字段与失败映射一致。
限制：全品类数据不天然符合设备 SKU 模型，复用基础设施不等于复用所有领域语义。
依据：ADR [0007](adr/0007-v1-v2-api-compatibility.md)、[数据边界](data-sources.md)。

### G. 区分组件得分、工作流成功和可观测性

问题：意图分类正确不代表多轮任务完成；语义事件不能当作真实节点计时。
做法：组件用无 Gold 输入/观测文件，工作流用公开 HTTP；
对照核验数据/Gold/门禁指纹，从原始观测重算。语义 Trace 解释路径，OTel 实测 Node，
API run 与实际 Graph invocation 分开计数，日志不保存隐藏推理。
验证：缺失/错误样本留在分母、审核过期拒绝、Model Mock → V2 回归、
幂等重放不虚增 Node span、interrupt 不包含人类等待、并发上下文和遥测失败隔离。
限制：独立人工 holdout 尚未完成，本地 Dashboard 不等于线上告警/SLO。
依据：ADR [0008～0011 索引](adr/README.md)、[Eval](../eval/README.md)。

### H. 交付验收检查信任与真实数据，不只看 healthy

问题：共享代理 Key 不能区分访客；服务 ready 也不保证示例可回答。
做法：代理服务身份与签名访客 owner 分离；先核验再恢复页面历史；
CI 用隔离合成配置演练 HTTPS/恢复/回退，真实发布另验现有依赖与 HTTP 入口。
通知的自动消失与身份错误的阻断状态也分开。
验证：跨访客读/删拒绝、双标签身份漂移、延迟响应竞态、原生旧主机兼容；
价格空库/缺型号、政策相关但证据不足分别定位，没有放宽事实门槛迎合示例。
限制：HTTP 内网例外没有链路加密，匿名访客不是企业登录；目标页面人工验收单列。
依据：ADR [0017](adr/0017-browser-visitor-identity.md)、[0019](adr/0019-controlled-deployment-and-offline-ci.md)。

## 3. RAG / 数据工程补充故事

- **可恢复流水线**：分页完整性、稳定 ID/内容哈希、SQLite 抓取状态、OCR sidecar、
  失败项 lineage；保留无法恢复附件，而非隐藏失败。
- **模型感知切分**：结构化条款/表格切分后用真实 tokenizer 检查输入，避免 embedding 静默截断。
- **分层证据判断**：Dense/BM25 + RRF → 重排 → 充分性 Judge → 引用生成；
  区分召回、相关性和“能否回答”，高检索分数不是答案依据。
- **评测驱动定位**：独立检查检索、错误拒答/回答、引用与必要事实；
  历史并发延迟暴露排队，不能只展示串行成功样例。

## 4. 简历 bullet 候选

根据实际岗位选择 2～3 条，不把所有实现列成技术名词清单：

- 基于 LangGraph 构建受约束查询 Agent，实现规则优先的混合理解、类型化工具路由、
  多轮补槽/命令确认及有界失败恢复，并保持业务规则与框架隔离。
- 设计查询作用域执行收据与三层幂等，支持 SQLite 重启续聊、JSON/SSE 重放、
  owner/TTL 约束及停服整库新目录恢复，区分主动刷新与重复执行。
- 以 Compatibility Adapter 复用既有 RAG/价格 Tool，同步 OpenAPI、生成 TS、
  运行时校验、领域 Renderer 与独立 Eval，避免复制业务实现。
- 建立 Understanding 组件与 V2 多轮分层评测、审核冻结/同样本对照及脱敏 Trace/OTel；
  48 条合成 development 上 Macro-F1 0.7068→1.0000，明确不是独立 holdout。
- 完成单实例内网交付，覆盖匿名访客隔离、离线 CI、备份/回退与旧主机受限兼容；
  真实物流接口保留未开放状态，不以 Mock 代替互通。

## 5. 面试演示与诚实边界

演示顺序：无 Key Demo → 缺槽/澄清 → 刷新恢复 → JSON/SSE 重放 →
同单号新查询 → 故障注入 → Node Trace → 查看评测差异。
严格资费再演示“产品/范围确认、改条件确认失效、来源/金额依据”，入口见[开发指南](development.md)。

不要说：五个真实工具均上线、意图理解准确率 100%、模型从不编造、供应商只计费一次、
支持多副本高可用、HTTP 达到公网安全要求、所有政策现行适用或价格覆盖所有型号。
真实数据/费用、独立 holdout、性能分位数、人工验收均需对应证据，不能拿工程测试数量替代。
