# 项目文档导航

本文档是 `docs/` 的入口。项目事实以代码、配置与测试为准。当前仓库是技术
Demo，文档只描述已经实现或已经确认的模块边界，不补写完整业务背景，也不预设
尚未稳定的后续场景。

## 当前版本基线

| 模块 | 当前版本 | 职责 |
| --- | --- | --- |
| `apps/offline-pipeline` | `0.2.0` | 政策采集、附件解析、OCR、切分、向量化与 Milvus 写入 |
| `apps/rag-api` | `0.5.1` | 政策检索、重排、证据约束回答与引用 |
| `apps/assistant-api` | `0.3.2` | 单轮工具编排、显式模式路由、政策问答与设备价格查询 |
| `apps/chat-web` | `0.2.0` | 浏览器聊天界面与流式响应展示 |
| `eval` | `0.3.0` | RAG、Assistant 黑盒评测与门禁 |
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
  `Proposed` 状态的 Query Understanding、LangGraph 状态图与 checkpoint/interrupt、
  类型化路由、受限 Agent Loop、故障处理、分阶段交付与验收方案。该文档不代表当前
  代码已经具备这些能力。

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
