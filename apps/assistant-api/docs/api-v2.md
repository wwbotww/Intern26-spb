# Agent V2 接口与客户端接入

[Assistant API](../README.md) · [项目文档导航](../../../docs/README.md)

适用：显式装配的 Stateful Agent。默认 V1 见[Assistant V1](api-v1.md)；
当前部署能力见[状态页](../../../docs/current-status.md)。字段以[版本化 OpenAPI](../../../docs/openapi/assistant-agent-v2.openapi.json)
和[公开 DTO](../src/spb_assistant_api/api/agent_schemas.py)为准。

## 1. 地址与身份

后端原始路径为 `/v2/agent/...`；浏览器经同源代理使用 `/api/v2/agent/...`。
受控部署只公开必要业务路径，不通过 Web 暴露 metrics、ready、OpenAPI 或调试接口。

| 调用方式 | 身份 | 约束 |
| --- | --- | --- |
| 浏览器 | 代理注入服务 Key，后端签名 Cookie 决定 owner | JS 不持有 Key；核验 session_ref 后才能恢复本地历史 |
| 其他可信服务 | 管理员分配的 Bearer / X-API-Key | key-scoped owner，不伪装成浏览器，不代表终端用户 |
| 本地五能力 Demo | 显式关闭鉴权 | 只监听本机，禁止用于真实业务或部署 |

浏览器模式先 `POST browser-session`，JSON `{"reset":false}`，响应
`{session_ref, expires_at}`。Cookie 为 HttpOnly，JS 只能获得非秘密的 session_ref。
后续业务请求带 `X-Agent-Session`，同源发送 Cookie；不安全方法须有精确 Origin。
catalog_v2 浏览器另须带 `X-Agent-Contract: product-price-v1`，用于能力、消息和快照的兼容检查，
不是身份凭据；缺失/不符在 SSE 或状态写入前返回 409 `agent_client_upgrade_required`。
普通核验不重新 Set-Cookie、不延长绝对期限；失效后用户显式 reset 才建立新身份。
重建不撤销所有旧 Token、不自动删除旧服务端会话；旧本地 pending 不可换身份重发。

## 2. 接口列表

| 方法 / 路径（相对 /v2/agent） | 用途 |
| --- | --- |
| POST /browser-session | 浏览器代理专用的建立/核验/重建身份；条件挂载 |
| GET /capabilities | 五类意图、available、输入描述；动态选项仍以当前 interrupt 为准 |
| POST /messages | 新建、补槽/确认、恢复或已完成会话的新查询；JSON/SSE |
| GET /conversations/{id} | owner/TTL 校验后读取最新持久停止点；不执行 Graph、不续期 |
| DELETE /conversations/{id} | 校验 owner 后清理会话；同 owner 重试 204 |
| GET /health/ready | 运维专用，不是浏览器业务路由；无真实付费探测 |

能力列表存在不代表全部 available，也不代表上游此刻在线。不要在客户端绕过服务端能力门禁。
每个 profile 只有一个价格能力：catalog_v2 为 `product_price`，device_v1 为 `device_price`。

## 3. 消息请求

`Idempotency-Key` 必填，1～128 个可见非空白 ASCII 字符；建议每条新消息使用随机 UUID。
失败重试保留原键与业务内容；新查询使用新键。不要在键中放邮件号或个人信息。

~~~json
{
  "conversation_id": null,
  "message": "帮我查一下邮件",
  "explicit_intent": null,
  "confirm_overwrite": false,
  "stream": false
}
~~~

- 新会话需要 message；文本去除首尾空白后为 1～2000 字符。
- 后续消息带服务端返回的 conversation_id。恢复意图澄清时可只提交 explicit_intent。
- 当前 profile 允许 policy、tracking、delivery_time、postage 以及一个价格意图；不能显式选 unknown。
  catalog_v2 新请求使用 product_price；旧 device_price 返回 409 `agent_price_intent_upgraded`。
  device_v1 环境则不开放 product_price；客户端应读取能力目录，而不是直接遍历完整历史枚举。
- confirm_overwrite 只确认冲突覆盖，不等于资费最终范围确认。
- required_inputs 中的 mail_no、product_code、postage_confirmation 是 UI 输入描述，
  **不是任意可添加的请求顶层字段**。普通槽位将值/选择序列化为 message，额外字段被拒绝。
- 商品候选是显式例外：从 required_inputs 的 price_candidates 取得服务端 token，发送
  `price_selection: {candidate_token}`。必须带 conversation_id，不能同时改 message、explicit_intent
  或请求 confirm_overwrite；不能提交数据库 ID。完整合同见[价格公开协议](integrations/product-price-public-release.md#2-候选请求与幂等)。
- 不提交 history、owner_id、Graph State、工具名或供应商配置；状态由服务端持有。

## 4. 浏览器最小调用顺序

以下演示同源集成；真实环境消息可能调用已装配的 RAG/模型，运行前明确访问范围。
组件正式接入宜复用现有 `agent-api.ts` 的错误/Schema/SSE 校验。
示例假设代码位于 `apps/chat-web/src/`；复用项目 ID helper，兼容批准的内网 HTTP。

~~~javascript
import { createId } from './id'

const base = '/api/v2/agent'
const bootstrap = await fetch(base + '/browser-session', {
  method: 'POST', credentials: 'same-origin',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ reset: false }),
})
if (!bootstrap.ok) throw new Error('请先处理身份核验失败')
const identity = await bootstrap.json()
const headers = {
  'X-Agent-Session': identity.session_ref,
  'X-Agent-Contract': 'product-price-v1',
}
const capabilities = await fetch(base + '/capabilities', {
  credentials: 'same-origin', headers,
})
if (!capabilities.ok) throw new Error('能力查询失败')
const catalog = await capabilities.json()
const priceCapability = catalog.find(
  item => item.intent === 'product_price' || item.intent === 'device_price',
)
if (!priceCapability?.available) throw new Error('商品价格查询暂不可用')

// 每条新消息创建一次；仅重试时复用这个 key 和 payload。
const key = createId()
const payload = { message: '查询 iPhone 17 256GB 的参考价格',
  explicit_intent: priceCapability.intent, stream: false }
const response = await fetch(base + '/messages', {
  method: 'POST', credentials: 'same-origin',
  headers: { ...headers, 'Content-Type': 'application/json', 'Idempotency-Key': key },
  body: JSON.stringify(payload),
})
if (!response.ok) throw new Error('按状态码处理错误，勿自动换键重发')
const result = await response.json()
~~~

这是调用顺序示意，不是“每次都能返回该型号价格”的保证。后续补槽复用
result.conversation_id、新建消息键，并按 required_inputs 提交；需要进一步确认时继续等待用户。

## 5. 响应与恢复

响应包含 request_id、conversation_id、turn_id、phase、intent、next_action、
required_inputs、result、failure、reply、warnings。只消费公开字段：

| phase / next_action | 行为 |
| --- | --- |
| waiting_user / collect_slots 或 clarify_intent | 展示服务端要求的控件；同会话的新消息恢复 |
| completed / complete | 渲染 success、partial、no_match 或 need_more_info；不是一律“有答案” |
| handoff / handoff | 无可执行自动路径；当前没有人工工单转交 |
| failed / failed | 展示固定失败摘要，不生成事实卡片 |

`result.type` 决定 Renderer；轨迹的 `result.provenance` 保留配置来源、
profile、观察时间和历史完整性；不是供应商认证，也不表示完整轨迹。
资费通过 `result.quote_basis` 给出金额口径、范围、费用包含关系和独立来源，
不暴露内部计价身份/指纹；旧快照缺字段应显示未知，不能补 CNY、零金额或当前时间。
`result.type=product_price` 时，`result.data` 使用独立白名单 DTO，保留设备/生鲜身份、金额字符串或无金额状态、
原始/标准单位、来源地区及时间；不包含数据库 ID 或查询指纹，不将旧观察伪装成当日价格。
字段和候选循环见[价格公开协议](integrations/product-price-public-release.md)。

刷新时先核验访客身份，再 GET 指定 conversation_id 的停止点。该读取同样验证 owner、TTL 和会话锁，
不调用工具/模型，不延长会话或候选期限。执行中返回冲突；不是会话列表或完整历史导出接口。
存在未确认请求时保留原键供用户选择重试，不能用旧快照覆盖它或自动换键推进。
旧 State 1/2/3 的未完成价格返回 `price_query_restart_required`；保留可读旧历史，提示新建查询，
不重算旧价格。已完成的合法消息收据可按原键免执行重放，详见[状态升级](integrations/product-price-public-release.md#3-客户端与状态版本协调)。

删除清理 checkpoint、消息/Tool 收据并保留必要 tombstone/创建幂等约束，避免重放复活。
不同 owner 和不存在的会话均不泄露存在性。恢复备份的删除限制见[运维](operations.md)。

## 6. SSE

请求 `stream=true` 返回 `text/event-stream`；使用 POST fetch 读取，而非只支持 GET 的 EventSource。
所有已知事件的 `schema_version` 都是字符串 `"1"`：

~~~text
status → state → (input_required | result) → [delta] → done
                                      异常 → error（终止）
~~~

- done 是经过响应 schema 校验的最终快照；error 也是终止事件。
- 已建立流后不能再改 HTTP 状态，error 内携带原始 HTTP 语义/稳定原因。
- 未收到 done/error 即断开：保留原请求/键，用户选择安全重试；清除部分内容后重绘。
- JSON 与 SSE 的 stream 选择不参与业务指纹，跨传输重放保持同一会话/turn。
- 未实现 Last-Event-ID 偏移续传；这是整消息幂等重放，不是从丢失 token 的位置接续。
- 当前 delta 是停止态的用户可见回复投影，不承诺模型 token 级流。
- 停止浏览器读取不保证服务端执行已取消，不能据此声称零上游调用。

本地快照最多保存 30 分钟，身份匹配后才能展示；服务端 TTL 另有配置。
指定会话的 owned snapshot GET 不提供跨设备身份或任意历史枚举。

## 7. 错误处理

| 状态 | 常见原因 | 客户端处理 |
| --- | --- | --- |
| 400 / 422 | 重复安全头、非法 schema/输入 | 修正请求，勿盲重试 |
| 401 / 403 | 服务/访客身份、Origin/代理角色错误 | 先修正身份或入口，不更换 owner 强行恢复 |
| 404 | 会话不存在、已删或不属于调用者 | 不枚举会话；明确新建 |
| 409 | 身份漂移、TTL、并发、幂等冲突、契约/价格状态升级或预算问题 | 按稳定 code 区分刷新客户端、新建查询或安全重试；不统一重发 |
| 429 | 共享限流 | 遵守 Retry-After；重建访客不能绕过配额 |
| 502 / 503 / 504 | 合同失败、依赖/存储故障、运行总预算超时 | 仅按 retryable / 稳定原因处理 |
| 500 | 未分类内部错误 | 用 Request ID 排查，不展示内部异常 |

可预期业务失败可能为 HTTP 200 + phase=failed，no_match 也可正常返回 200。
HTTP 成功、Workflow 成功和业务有结果是三个不同判断。

## 8. 契约维护

同时更新 API DTO、OpenAPI artifact、生成 TS、前端运行时校验与独立 Eval 镜像。
`npm --prefix apps/chat-web run check:agent-types` 校验生成物；契约/重放/隐私负例必须回归。
供应商字段不得直接透传为公开 schema，详见[模块边界](../../../docs/workspace-architecture.md)。
