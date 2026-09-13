# Chat Web

[项目全貌](../../README.md) · [全局模块边界](../../docs/workspace-architecture.md) · [当前状态](../../docs/current-status.md)

Vue + TypeScript 客户端，通过同源 `/api` 代理访问 Assistant，不直接连接数据库、RAG 或供应商。
V1 和 Agent UI 共存，模式在构建时确定。服务 Key 只保存在开发代理/Nginx，不能进入 `VITE_*`。

## 配置与运行模式

所有命令从仓库根目录执行。Node 要求 `^22.12.0 || >=24.0.0`；
依赖安装见[工作区开发指南](../../docs/development.md)，字段见 [.env.example](.env.example)。

| 配置 | 负责什么 |
| --- | --- |
| `VITE_ASSISTANT_UI_MODE=legacy/agent` | V1 / V2 界面；默认 legacy |
| `VITE_AGENT_BROWSER_SESSION=true/false` | Agent 访客身份协议；必须匹配后端，默认 false |
| `CHAT_WEB_ASSISTANT_API_URL` | 仅开发代理使用，默认 `http://127.0.0.1:8081` |
| `CHAT_WEB_ASSISTANT_API_KEY` | 仅服务端开发代理使用；不能转成公开前端变量 |

改运行时容器环境不会更改已构建 JS 中的 VITE 开关。真实代理/构建配套见
[跨服务运维](../../docs/operations.md)和[部署手册](../../deploy/agent/README.md)。

## Agent Demo

先按 [Assistant 五能力 Demo](../assistant-api/docs/local-development.md#五能力-demo)启动 API。
再在另一个终端运行：

~~~bash
POSTAGE_OFFLINE_API_PORT=8081 \
npm --prefix apps/chat-web run dev -- --config vite.postage-offline.config.ts
~~~

打开 http://127.0.0.1:13003/。该隔离配置不加载 dotenv，显式启用 Agent UI、关闭浏览器身份，
仅用于回环 fixture。配置名源于资费，但可用上述变量指定五能力 Demo 的 API 端口。

## 资费协议 Demo

先按 [Assistant 资费夹具](../assistant-api/docs/local-development.md#资费协议离线演示)
启动 18083 API，再运行：

~~~bash
npm --prefix apps/chat-web run dev -- --config vite.postage-offline.config.ts
~~~

同样访问 http://127.0.0.1:13003/，不能与上一模式同时占用端口。
合成 Key 仅供固定 fixture，不是业务凭据。产品/范围确认、报价和失败预期以夹具文档为准。

## 商品价格 Demo

先按 [Assistant 商品价格夹具](../assistant-api/docs/local-development.md#商品价格与候选恢复演示)
启动固定 18086 API，再运行：

~~~bash
npm --prefix apps/chat-web run dev -- --config vite.browser-session.config.ts
~~~

打开 http://127.0.0.1:13006/。该配置不加载 dotenv，显式启用 Agent UI 与合成访客身份，
用于验证商品候选、卡片及刷新恢复，不可连接真实后端或对外部署。
它与 13003 的无身份五能力/资费 Demo 是不同装配。

## 常规开发与真实后端

先在本服务本地 `.env` 配置获准的代理目标和 Key，核对 API/UI/身份模式；不要覆盖已有配置。
普通 Vite 会读取本地环境，并非离线隔离入口：

~~~bash
npm --prefix apps/chat-web run dev -- --host 127.0.0.1
~~~

默认端口为 3000。浏览器身份的 HTTPS / 精确 Origin 或已批准的内网 HTTP 例外由整套入口配置，
不是关掉前端校验即可联通。专用 `vite.browser-session.config.ts` 只用于合成身份 QA，
不要拿它连接真实服务。入口失败时先核对身份，再决定是否显示本地历史。

## 代码边界

| 位置 | 自身职责 |
| --- | --- |
| [main.ts](src/main.ts)、[App.vue](src/App.vue)、[api.ts](src/api.ts) | 模式选择、V1 界面及旧单轮协议 |
| [AgentApp.vue](src/AgentApp.vue)、[agent-ui-model.ts](src/agent-ui-model.ts) | Agent 交互编排与 UI 状态；不执行业务 Tool |
| [agent-api.ts](src/agent-api.ts) | HTTP/SSE、访客核验与运行时 schema 校验；组件不自行解析 wire JSON |
| [AgentSlotForm.vue](src/components/AgentSlotForm.vue)、[agent-slot-input.ts](src/agent-slot-input.ts) | 普通槽位转 message；商品候选转独立 price_selection，不把 token 或身份 JSON 混进文本 |
| [结果组件](src/components/results/) | 按公开 Result 类型渲染；不展示供应商原始字段或自行计算报价 |
| [ProductPriceResult.vue](src/components/results/ProductPriceResult.vue)、[product-price-contract.ts](src/product-price-contract.ts) | 商品卡片与严格事实校验；区分设备/生鲜、原始/标准单位、无金额状态及来源日 |
| [agent-session.ts](src/agent-session.ts) | 有期限的公开 UI 快照和待重试请求；不是后端 checkpoint |
| [agent-examples.ts](src/agent-examples.ts) | 示例目录；能力可用不保证任意示例都可回答 |
| [id.ts](src/id.ts)、[use-transient-notice.ts](src/use-transient-notice.ts) | 请求标识兼容与临时通知生命周期；阻断身份错误不能随通知消失 |

`session_ref` 用于检测身份漂移，不是授权凭证。刷新先核验身份，再恢复本地历史并用指定会话的
owned snapshot GET 校准持久停止点；读取不执行 Graph、不续期。待重试请求保留原键，不自动重发。
继续执行由服务器验证 owner、TTL 和幂等键；没有跨设备历史列表接口。
客户端发送 `X-Agent-Contract: product-price-v1`，配套 catalog_v2 的商品能力和 State 4；
旧完成 device_price 卡片仍可读，旧未完成价格要求重新查询。
已有完成消息重放不能自动换键；主动新查询才使用新请求标识。

## 契约与验证

接口事实见 [Assistant V2](../assistant-api/docs/api-v2.md)或 [V1](../assistant-api/docs/api-v1.md)。
根 [OpenAPI](../../docs/openapi/assistant-agent-v2.openapi.json)由后端维护，
[生成脚本](scripts/generate-agent-api-types.mjs)据此产生 `src/generated/agent-api.ts`，不手改生成物。
生成类型不替代网络响应的运行时校验。

~~~bash
npm --prefix apps/chat-web test
npm --prefix apps/chat-web run check:agent-types
npm --prefix apps/chat-web run build -- --config vite.postage-offline.config.ts
~~~

只有契约变更时运行 `npm --prefix apps/chat-web run generate:agent-types`，核对生成 diff 后再检查。
完整双模式/身份构建矩阵见 [CI](../../.github/workflows/agent-ci.yml)。
依赖审计 `npm --prefix apps/chat-web run audit:dependencies` 需要额外 npm 网络。
