# Assistant API

[项目全貌](../../README.md) · [全局模块边界](../../docs/workspace-architecture.md) · [当前状态](../../docs/current-status.md)

本服务是统一业务查询入口：V1 保留显式单轮 Tool，V2 用 LangGraph 编排理解、路由、
澄清、补槽、执行和恢复。它通过 HTTP 调用 RAG、只读访问价格库，不生产数据；
物流和模型分别通过受控 Adapter 装配，不让模型决定业务事实或任意调用工具。

## 从哪里开始

| 任务 | 本服务文档 |
| --- | --- |
| 无 Key 跑通工作流 / 资费 Mock | [本地开发](docs/local-development.md) |
| 接入当前会话接口 | [Agent V2 API](docs/api-v2.md) |
| 维护旧单轮入口 | [Assistant V1 API](docs/api-v1.md) |
| 修改 Understanding、Routing、State、Loop 或失败行为 | [Agent 运行设计](docs/runtime.md) |
| 配置、身份、状态库、备份恢复与排障 | [服务运维](docs/operations.md) |
| 接模型 / 物流供应商 | [Query Model](docs/integrations/query-model.md)、[轨迹](docs/integrations/tracking.md)、[资费](docs/integrations/postage.md) |
| 适配全品类价格 V2 数据 | [价格消费合同与内部查询](docs/integrations/product-price.md)、[MySQL 隔离门禁](../../deploy/price-query/README.md)（尚未装配到查询入口） |

完整部署涉及 Web、网络、数据与镜像，归[跨服务运维](../../docs/operations.md)和
[部署操作手册](../../deploy/agent/README.md)，不是单独启动本服务即可完成。

## 启动与配置

命令均从仓库根目录执行，先完成[工作区安装](../../docs/development.md)。
[.env.example](.env.example)是字段入口；普通 `AssistantSettings` 依次读取本服务 `.env`
和根 `.env`，后者同名值覆盖前者，进程环境优先。不输出或覆盖已有私有文件。

| 入口 | 作用与边界 |
| --- | --- |
| `spb-assistant-api` | 普通 Settings；Agent 默认关闭，此时只提供 V1 |
| `spb-assistant-agent-demo` | 五能力 fixture；使用本地开发页的显式关闭参数，不对外部署 |
| `tests.postage_p3_fixture` | 不读取 dotenv 的严格资费 Mock；无真实 transport |
| `python -m spb_assistant_api.deployed_app` | 不读取 dotenv；要求鉴权、浏览器身份、managed SQLite 与单 worker |

已经配置**获准的** RAG/价格只读依赖，且确认 `ASSISTANT_AGENT_ENABLED=false` 时，
V1 本地启动可用：

~~~bash
ASSISTANT_HOST=127.0.0.1 ASSISTANT_PORT=8081 .venv/bin/spb-assistant-api
~~~

默认未填凭据不会获得真实能力。查看根目录的[联调组合](../../docs/development.md)
为后端选择匹配的 Web；不要用普通入口或 Demo 替代受控发布入口。

## Agent 内部分层

完整状态与执行不变量见[Agent 运行设计](docs/runtime.md)。
下表代码路径相对于 [src/spb_assistant_api](src/spb_assistant_api/)。

| 层 | 代码入口（assistant-api 内） | 扩展方式 |
| --- | --- | --- |
| Domain | `domain/{understanding,commands,slots,results,failures,ports}.py` | 类型化业务概念与 Port，不引入供应商字段或 LangGraph |
| Application Service | `services/` | 理解、地区解析、槽位合并、资费前置条件、工具执行与结果校验 |
| Policy / Workflow | `workflow/{policy,graph,runtime,state}.py` | 纯策略决定动作；LangGraph 执行节点、interrupt/resume 与 checkpoint |
| Adapter | `adapters/` | MySQL、HTTP、模型、邮政协议、SQLite/Checkpointer 的边界转换 |
| API / Security | `api/`、`security/`、`middleware/operations.py` | 公开 DTO、Cookie/Origin/owner、请求预算与脱敏投影 |
| Composition / Operations | `configured_agent.py`、`deployed_app.py`、`storage_*.py` | 装配/开关、资源归属、受控存储与部署；不写进 Node |
| Observability | `observability/`、`workflow/instrumentation.py` | 固定字段 Trace、指标、实际节点计时、组合根管理 SDK 生命周期 |

LangGraph import 限制在 Workflow 与 checkpointer adapter 边界。DTO 不直接暴露 Graph State；
公开 API、内部 Domain、Eval 镜像及 Web 运行时校验各自服务不同信任边界。

## V1 / V2 如何复用

V1 接收显式 `policy/device_price`，一次请求只执行一个 Tool，无会话历史。
V2 的 Compatibility Adapter 只把类型化 Command 转换为既有 Tool 调用，再投影类型化结果：

~~~text
V2 Command → legacy_agent_tools → 同一 V1 AssistantTool
  → 共享 ToolResult 校验 → Agent Result 校验 → 公开 API / Renderer
~~~

政策的检索、拒答和引用校验，价格的产品级匹配/规格过滤都不复制。
Agent 借用 V1 Tool；应用先初始化 V1 Registry，再启动 Agent，关闭时顺序相反。
HTTP 连接池和数据库连接不能由多个层重复关闭。

该复用解决已有两类查询；全品类 V2 数据并不天然符合 DevicePriceRecord，
需要新的查询/结果边界，不能仅更改 SQL 表名。
目标改造、价格资源生命周期调整及兼容门禁见
[全品类价格实施计划](../../docs/product-price-implementation-plan.md)；目前尚未替换上述现有实现。

## 验证与扩展

~~~bash
.venv/bin/pytest apps/assistant-api/tests
~~~

- 改接口：同步 [OpenAPI](../../docs/openapi/assistant-agent-v2.openapi.json)、[Web](../chat-web/README.md)
  类型/运行时校验及 [Eval](../../eval/README.md)镜像，再验 JSON/SSE、重放和隐私负例。
- 改物流：本地协议/语义/熔断与真实互通分别验收；供应商字段不直接透传为公开结果。
- 改持久化：同时验 owner、TTL、创建/消息/Tool 收据、checkpoint、租约及停服恢复。
- 改意图模型：默认离线回归不调用付费模型，真实联调单独授权并限制预算。

这里不重复记录开发阶段和测试总数；未来任务见[路线图](../../docs/roadmap.md)，
设计取舍见[根 ADR](../../docs/adr/README.md)。
