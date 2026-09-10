# 项目文档导航

本文档是 `docs/` 的入口。项目事实以代码、配置与测试为准。当前仓库是技术
Demo，文档只描述已经实现或已经确认的模块边界，不补写完整业务背景，也不预设
尚未稳定的后续场景。

## 当前版本基线

| 模块 | 当前版本 | 职责 |
| --- | --- | --- |
| `apps/offline-pipeline` | `0.2.0` | 政策采集、附件解析、OCR、切分、向量化与 Milvus 写入 |
| `apps/rag-api` | `0.5.1` | 政策检索、重排、证据约束回答与引用 |
| `apps/assistant-api` | `0.3.6` | V1、显式 Stateful Agent V2、理解观测与逐 Node 遥测 |
| `apps/chat-web` | `0.2.0` | 浏览器聊天界面与流式响应展示 |
| `eval` | `0.7.0` | 黑盒／组件评测、同样本对照与人工审核冻结 |
| `packages/contracts` | `0.1.0` | 离线写入与在线读取共享的数据契约 |

版本号描述当前仓库基线；部署环境仍应以实际镜像标签和 `/health` 返回为准。

## 当前实现

- [工作区架构](workspace-architecture.md)：模块边界、依赖方向、数据流和复用边界的总览。
- [RAG API 调用契约](api-reference.md)：面向调用方的 `/v1` 接口、鉴权、错误与流式协议。
- [RAG API 实现说明](rag-api.md)：检索、重排、证据判断、生成和可观测性。
- [Assistant API](assistant-api.md)：当前统一入口、显式 `policy` / `device_price` 路由、设备匹配与响应契约。
- [部署与运行](deployment.md)：当前三服务 Compose 拓扑、配置、健康检查、安全边界与运维注意事项。
- [评测说明](../eval/README.md)：数据集、指标、运行方式、报告和质量门禁。

## 下一阶段规划

- [LangGraph Stateful Agent Workflow 实施方案](agent-workflow-implementation-plan.md)：
  总体为 `In progress`；阶段 0～2、3A、4A～4D、5A～5E 的本地切片已完成。
  模型 Adapter、真实烟测及 development 对照已完成；3B-T 的 T0～T3 契约 / Adapter、
  来源 / 新鲜度 / 迁移、受控 V2 / Web 已本地验证并收尾；接口不可达，T4 暂缓。
  3B-P 的 P0～P3、6A-1～6A-3 本地 / 合成通路完成，包含访客隔离、整库恢复、独立
  HTTPS 部署与 V1 回退；工具链收口后当前 Python 1044 项、Web 70 项，完整 npm audit 0。
  CI 文件及 moderate 门禁已实现，下一步获授权提交推送后验证远程执行；P4、holdout 和完整阶段 6 未完成。
- [Phase 1 Agent Kernel 与 Fake Tracking](agent-kernel-phase1.md)：已实现的状态图、模块
  边界、预算、执行收据、Failure 路径、测试证据和未实现范围。
- [Phase 2 Hybrid Understanding 与 SQLite 持久化](agent-kernel-phase2.md)：五意图规则、
  Structured Model schema gate、跨轮合并、`AsyncSqliteSaver`、元数据/幂等、TTL、并发
  和重启恢复证据。
- [阶段 2 补齐：真实理解模型接入](agent-query-model-integration.md)：DeepSeek Adapter、
  显式配置、生命周期、单次有界调用、Mock / V2 验证与真实联调。
- [2026-09-07 模型真实烟测](agent-query-model-live-smoke-20260907.md)：5 次模型尝试、
  三类补槽、unknown／超时回退、7 次免模型幂等重放与用量边界；不是 holdout。
- [Phase 3A Gateway 与可靠性基础](agent-kernel-phase3a.md)：时限/资费类型化 Tool、共享
  单次 HTTP 边界、有界退避、能力级熔断和接口到达前的合同测试证据。
- [Phase 3B-T 邮政轨迹契约与适配器](agent-kernel-phase3b-tracking.md)：T0/T1 的
  provisional 表单 / 签名 / Gateway 与 84 项协议测试；真实互通仍待确认。
- [T2 查询新鲜度、执行收据与轨迹来源](agent-kernel-phase3b-tracking-t2.md)：58 项新增
  测试、State v3 / SQLite 迁移及邮政 Mock V2 集成。
- [T3 受控装配、公开来源与 Web 闭环](agent-kernel-phase3b-tracking-t3.md)：56 项新增 Python
  与 12 项 Web 用例、配置 / 生命周期、语义熔断、来源契约及浏览器验收；已完成本地收尾，T4 暂缓。
- [Phase 3B-P 资费接口评审与切片建议](agent-kernel-phase3b-postage-analysis.md)：CSB / 双层
  签名、产品 / 地区 / 计费条件、金额口径及 P0 初始差距；外部合同仍待确认。
- [P1 资费领域契约与可执行条件](agent-kernel-phase3b-postage-p1.md)：86 项新增测试、
  产品 / 地区目录、整数克、报价观察、上下文冻结与恢复保护；保持生产未装配。
- [P2 资费协议与离线 Gateway](agent-kernel-phase3b-postage-p2.md)：160 项新增测试、双层签名、
  四层响应检查、语义熔断、profile 绑定与可复跑 Graph / SQLite 烟测；只接受 MockTransport。
- [资费扩展缺口台账](agent-kernel-phase3b-postage-gaps.md)：外部合同临时处理 / 所需证据，
  以及 P3 内部关闭项、自然语言残余局限与后续交付顺序。
- [P3 资费公开契约与离线 Web 闭环](agent-kernel-phase3b-postage-p3.md)：命令绑定确认、
  白名单报价依据、独立离线工厂、13 场景 / 28 Turn Eval 与本地浏览器验收。
- [6A-1 浏览器访客身份](agent-kernel-phase6a1-browser-identity.md)：代理服务 Key 与访客 owner
  分离、Cookie/同源校验、核验后恢复、多标签页竞态与默认关闭配置；54 Python / 15 Web 新增。
- [6A-2 SQLite 持久化与恢复](agent-kernel-phase6a2-sqlite-recovery.md)：受控目录 / 整库租约、
  含 WAL 的停服快照、新目录恢复、44 项新增回归与独立断网 Docker 三卷演练；不覆盖旧库。
- [6A-3 受控部署与离线 CI](agent-kernel-phase6a3-controlled-deployment.md)：独立冻结镜像、
  HTTPS / Host / 公开路由、自动门禁文件和新卷恢复 / V1 回退合成演练；21 项新增回归，
  两个远程 CI job 已通过；[部署 runbook](../deploy/agent/README.md) 提供复跑和受控操作步骤。
- [6A-3 工具链安全收口](agent-kernel-phase6a3-ci-closeout.md)：Vitest 4.1.11、默认单测与
  dotenv / 开发代理分离、CI 全依赖 moderate 门禁、Node 22 构建内测试；3 项新增合同回归。
- [6A-4 内网单实例更新](agent-kernel-phase6a4-intranet-release.md)：保留明确批准的 HTTP，
  显式例外 / 私有 origin / 双访客隔离、原 RAG / 价格库复用与三项物流 unavailable。
- [Phase 4A Stateful Agent V2 JSON API](agent-kernel-phase4a.md)：显式装配的 V2 JSON、
  interrupt 投影、三层幂等、owner 隔离、外层 timeout、会话删除和 API 集成证据。
- [Phase 4B Versioned SSE 与 Stateful Agent Web](agent-kernel-phase4b.md)：稳定 SSE 投影、
  OpenAPI 类型生成、运行时事件校验、补槽/澄清 UI、刷新恢复、类型化 Renderer 和本地
  Demo 浏览器验收。
- [Phase 4C Agent Operations 与隐私安全可观测性](agent-kernel-phase4c.md)：独立 V2
  readiness、低基数指标、脱敏 Run Trace、lifespan janitor 调度和降级语义。
- [Phase 4D V1 Tool 复用与五能力 Agent 闭环](agent-kernel-phase4d.md)：共享 V1 合同、
  Policy/Device 兼容 Adapter、完整 Evidence 投影、五能力 Demo 和 Web 结果卡片。
- [Phase 5A Agent 多轮黑盒评测与质量门禁](agent-kernel-phase5a.md)：V2 HTTP 多轮 Runner、
  13 场景/17 Turn fixture、七项质量门禁、失败归因和可复现本地基线。
- [Phase 5B 可靠性故障矩阵、语义 Trace 与 Agent 报告对比](agent-kernel-phase5b.md)：
  checkpoint 增量语义 Trace、隐私白名单、故障恢复矩阵和严格同样本逐 Turn 对比。
- [Phase 5C Understanding 组件质量评测](agent-kernel-phase5c.md)：独立输入／观测契约、
  Intent／硬槽位 F1、失败与用量口径、有界调用、数据冻结和同样本对照。
- [2026-09-07 Understanding 对照证据](agent-understanding-comparison-20260907.md)：
  48 条 development 数据、20 次真实模型请求、14 条改善与成本／泛化边界。
- [Phase 5D 人工审核冻结与 V2 语义工作流回归](agent-kernel-phase5d.md)：跨文件污染检查、
  pending 人工审核、数据冻结，以及 13 场景／28 Turn 的离线 Mock 供应商集成证据；未批准真实 holdout。
- [Agent Workflow ADR](adr/README.md)：已接受的受约束 Agent、LangGraph Runtime、
  Hybrid Understanding、Memory Boundary、Failure Taxonomy、类型化路由和评测门禁决策。
- [Phase 5E 逐节点遥测与本地 Dashboard](agent-kernel-phase5e.md)：OTel 父子 span、
  采样/有界导出、Prometheus 指标、只读 Trace 下钻及独立 Docker 合成烟测。
- [Assistant Agent V2 OpenAPI](openapi/assistant-agent-v2.openapi.json)：Phase 4D JSON/SSE 与
  readiness 已在显式装配路径实现；默认生产装配和真实接口字段仍用于后续
  breaking-change 评审。

## 求职与复盘

- [AI 应用 / Agent 岗位技术复盘](project-retrospective.md)：只保留由当前实现支撑的量化结果、关键设计、工程权衡和面试素材。

## 维护规则

1. 文档只描述已由代码、配置或测试证明的能力，以及已经确认的当前约束。
2. API 字段与错误码以实现和测试为最终依据；修改接口时同步更新对应文档。
3. 性能数字必须注明版本、数据集、运行环境或“历史基线”，不得直接当作当前生产 SLA。
4. `.env`、密钥、内部 IP、组织身份和一次性交付步骤不进入仓库文档。
5. 未稳定方向只能写入明确标记为 `Proposed` 的规划文档；不得混入当前实现说明，
   未确认字段必须保留 provisional 标记和接口确认清单。
6. 面向特定交付对象的说明不作为长期项目文档；通用内容应提炼进对应主题。
