# 工作区开发与联调

本页只维护共同环境、联调组合与跨模块验证；具体启动、配置和内部调试归各模块。
所有文档中的命令，除非另有说明，都从**仓库根目录**执行。
不要复制覆盖已有 `.env`；服务器更新先读[跨服务运维](operations.md)。

## 1. 安装与全局回归

要求 Python 3.11+、uv、Node `^22.12.0 || >=24.0.0`（CI 为 Python 3.12 / Node 22）。
安装需要软件仓库网络；默认测试不需要真实 API Key。

~~~bash
uv sync --frozen --all-packages --group dev --python 3.12
npm --prefix apps/chat-web ci --include=dev --engine-strict
.venv/bin/pytest
npm --prefix apps/chat-web test
npm --prefix apps/chat-web run check:agent-types
~~~

只测一个 Python 模块时向 pytest 传入其 tests 目录。Web 类型/双模式构建和受控合成验收以
[CI 工作流](../.github/workflows/agent-ci.yml)为准。
依赖审计需要额外 npm 网络，不属于离线回归；具体命令见 [Chat Web](../apps/chat-web/README.md)。

## 2. 选择联调组合

先启动 API，再启动表中的 Web 配置；各用一个终端。五能力/资费 Demo 共用 13003，不能同时运行；
商品价格身份 Demo 用 13006。合成 Web 均不加载 dotenv，但后端隔离边界不同，应按对应步骤执行。

| 目标 | API 入口与步骤 | Web 入口与步骤 | 验证重点 |
| --- | --- | --- | --- |
| 第一次接手，无 Key 理解五能力 | [五能力 Demo](../apps/assistant-api/docs/local-development.md#五能力-demo)，8081 | [Agent Demo](../apps/chat-web/README.md#agent-demo)，代理到 8081 | 意图、补槽、合成结果、刷新继续 |
| 资费协议与确认通路 | [资费离线夹具](../apps/assistant-api/docs/local-development.md#资费协议离线演示)，18083 | [资费协议 Demo](../apps/chat-web/README.md#资费协议-demo)，默认代理 18083 | 产品/范围确认、报价依据、幂等和失败 |
| 连接已批准的真实依赖 | [Assistant 配置与入口](../apps/assistant-api/README.md) + [RAG](../apps/rag-api/README.md) | [常规开发与真实后端](../apps/chat-web/README.md#常规开发与真实后端) | API/UI 模式、服务 Key、Origin/身份、数据覆盖 |
| 验证部署边界，不接业务数据 | [Agent 合成部署演练](../deploy/agent/README.md) | 演练自带匹配 Web | HTTPS、双访客、备份/恢复、V1 回退 |
| 验证 V2 价格固定 SQL，不接业务数据 | [MySQL 5.7 / 8.4 隔离门禁](../deploy/price-query/README.md) | 不需要 Web | V2-only、SELECT 权限、当前状态/单位/关联和查询上限 |
| 验证商品价格理解，不接模型/数据库 | [Understanding 组件](../apps/assistant-api/docs/integrations/product-price-understanding.md) | 组件验证不需要 Web | 商品/寄递重量区分、联合条件、冲突及候选失效信号 |
| 验证商品公开协议与候选，不接公司库 | [catalog 合成夹具](../apps/assistant-api/docs/local-development.md#商品价格与候选恢复演示)，18086 | [商品价格 Demo](../apps/chat-web/README.md#商品价格-demo)，13006 | 访客身份、候选点击、设备/生鲜卡片、刷新与旧来源日 |

业务 fixture 的五能力 Demo 不等于真实物流互通；严格资费夹具也只有 MockTransport。
默认 Web 为 V1，Agent UI 与浏览器身份是构建时开关，不能只换后端。
离线采集/模型下载/重建 Milvus 不属于上述接手步骤，按 [Pipeline](../apps/offline-pipeline/README.md)
在取得目标写入权限后单独操作。

## 3. 联调后检查什么

1. API 与 Web 模式一致；依赖未装配时正常不可用，不注入 Fake。
2. JSON 与 SSE 保持相同业务语义；跨访客隔离、缺槽恢复和原消息幂等重放通过。
3. 调整接口后验证后端契约、根 OpenAPI、Web 生成类型/Renderer 与 Eval 镜像。
4. 修改共享数据契约时同时运行 Pipeline、RAG 和 contracts 回归，确认新旧数据兼容性。
5. 模型或真实业务联调另行授权并限定预算；离线测试通过不是这些真实结果的证明。

无需启动浏览器、无需网络的资费及商品价格公开 Workflow 门禁：

~~~bash
AGENT_EVAL_DIR=$(mktemp -d)
.venv/bin/python -m apps.assistant-api.tests.ci_offline_eval --output "$AGENT_EVAL_DIR/reports"
~~~

数据格式、组件审核/冻结、评测命令与退出码唯一维护于 [Eval 指南](../eval/README.md)。
项目进度写入[当前状态](current-status.md)，未完成范围写入[路线图](roadmap.md)，
不要在模块 README 新建第二份阶段表或复制一次性的测试总数。
