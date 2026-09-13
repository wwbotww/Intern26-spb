# 项目文档导航

[根 README](../README.md)讲项目全貌，本目录维护跨模块事实。
服务自己的运行方式、接口字段、实现机制和供应商适配，放在对应服务目录。
这是职责上的两层，不是要求所有资料都移到 `apps/`，也不是限制子目录深度。

## 第一层：项目全局

| 问题 | 唯一维护入口 |
| --- | --- |
| 现在完成了什么，哪些能力真实可用 | [当前状态](current-status.md) |
| 应用之间怎样协作，边界与复用如何 | [Workspace 架构](workspace-architecture.md) |
| 如何安装工作区、选择联调模式、跑全局验证 | [开发指南](development.md) |
| RAG、设备价格 V1、全品类 V2 的生产者与消费者是谁 | [数据源边界](data-sources.md) |
| 如何选择部署栈、配置入口安全、发布交接 | [跨服务运维](operations.md) |
| 如何维护旧 RAG / Assistant V1 / Web Compose | [V1 部署基线](deployment.md) |
| 阶段收口后保留哪些遗留、重新启动需要什么 | [遗留与可选路线图](roadmap.md) |
| 全品类 V2 已实施设计、交付门禁与替换缺口 | [全品类价格实施计划](product-price-implementation-plan.md) |
| 为什么这样设计 | [ADR 索引](adr/README.md) |
| 历史实验与发布依据是什么 | [压缩交付摘要](history/agent-delivery-summary.md) |
| 面试与简历可讲什么 | [技术复盘](project-retrospective.md) |

## 第二层：模块自身

| 入口 | 在这里维护 |
| --- | --- |
| [Assistant API](../apps/assistant-api/README.md) | 本地运行、V1/V2 接口、Agent 内部分层/工作流、状态库与排障、模型/轨迹/资费适配 |
| [RAG API](../apps/rag-api/README.md) | 启动与配置、检索/问答 API、重排与证据充分性机制 |
| [Chat Web](../apps/chat-web/README.md) | 双模式启动、代理/身份、组件边界、SSE/快照、类型生成与前端验证 |
| [Offline Pipeline](../apps/offline-pipeline/README.md) | 抓取/解析/OCR/切分/入库命令、产物与增量写入约束 |
| [Contracts](../packages/contracts/README.md) | 共享 collection、embedding 和 chunk 元数据契约 |
| [Eval](../eval/README.md) | 评测数据格式、审核冻结、命令、指标和门禁 |

部署脚本的执行步骤仍就近维护于 [Agent 部署](../deploy/agent/README.md)和
[存储演练](../deploy/storage/README.md)，不放进任意一个服务。

[Agent OpenAPI](openapi/assistant-agent-v2.openapi.json)是跨端版本化契约，保留原路径；
后端契约测试与 Web 类型生成直接引用它。接口使用说明在 Assistant 目录，不复制 JSON。
已有 ADR 保持根层统一编号，以便追踪 API、Web、Eval、存储和部署共同遵守的设计决策。

## 维护规则

1. 新文档先确定归属：只改变一个模块的操作或实现，放该模块；跨模块协作与取舍才进入根 `docs/`。
2. 模块 README 保持接手入口，专题较长再建模块内 `docs/`；不为简单模块建空目录或重复索引。
3. 当前状态回答“现在怎样”；路线图区分遗留与可选未来；历史摘要保存有日期的证据；ADR 保存决策理由。
4. 一个事实只维护一份。配置字段链接 `.env.example`/代码，接口链接契约，其他页面只作摘要和导航。
5. 小变更不新增阶段报告或跳转壳。删除的过程材料由 Git 恢复；保留来源和结论，不保留流水账。
6. 不提交原始附件、业务数据、个人问题、凭据、内部连接信息或完整私有运行报告。
7. 迁移需核对根、模块、Eval 与部署引用，检查链接/锚点/命令；旧 ADR 保留决策时点，必要时标明后续演进或新增替代决策。
   API 变更另验生成物和契约测试；不因提交或文档审查自动更新服务器验收日期。
