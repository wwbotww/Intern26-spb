# Phase 3B-T：邮政轨迹契约与适配器

> 更新日期：2026-09-09。T0～T3 已完成本地实现与收尾；接口暂不可访问，T4 真实互通暂缓。
> T0/T1 新增 84 个测试，当时全量 `527 passed`；T2 再新增 58 项，当时全量 `585 passed`。
> 本文保留协议契约与 T0/T1 证据；T2 的迁移 / 来源 / 新鲜度详见 [T2 说明](agent-kernel-phase3b-tracking-t2.md)。
> 随后 [T3](agent-kernel-phase3b-tracking-t3.md) 又新增 56 个 Python / 12 个 Web 用例，
> 当前全量为 Python `641 passed` / Web `29 passed`；受控装配已实现但默认未启用。
> 真实物流请求、付费模型请求均为 0；没有读取或修改本地 `.env`，没有发布 V2。
> 当前只证明“实现符合明确标注的本地 provisional profile”，不证明供应商互通。

## 1. 范围与实施顺序

已收到一份《轨迹查询接口文档》，版本 V1.0.0，日期 2019-11-25。它足以支撑
**单邮件号主动查询**的适配器和离线合同测试，但存在签名、响应接收方、事件码和
时间口径等不确定项。启动本切片时只有轨迹文档，不需要等其他合同齐全才推进查轨迹。
T3 收尾时已另收到资费文档，见 [资费接口评审](agent-kernel-phase3b-postage-analysis.md)；
其 Adapter 尚未实现，时限文档仍未提供。

本切片不实现推送回调、订阅轮询、批量邮件查询、XML/压缩传输、预计送达时间，
也不推导签收真实性、丢失判责或赔付结论。`batchNo` 只是可选请求字段，不代表批量能力。

| 阶段 | 目标与交付 | 当前状态 / 退出条件 |
| --- | --- | --- |
| T0：冻结 provisional 契约 | 字段映射、冲突清单、独立标记的合成 fixtures | 已完成；未获得供应商确认 |
| T1：协议与 Gateway | 表单传输、兼容签名、严格响应校验、领域投影和故障测试 | 已完成离线验证；默认关闭，装配由 T3 提供 |
| T2：Tool / Workflow 语义 | 中立来源信息、查询新鲜度、收据作用域、兼容迁移与回归 | 已完成离线验证；新查询取新结果，已保存执行重放不追加调用 |
| T3：受控 V2 / Web 集成 | 显式配置、鉴权、lifespan、readiness、公开来源与 Renderer | 已完成本地 / Mock 验收；含语义熔断，不等于真实互通 |
| T4：真实联调 | 确认协议与配置后，获授权进行小量测试并保留故障证据 | 用户确认接口不可达，暂缓；恢复后仍需合同确认和单独调用授权 |

以上是原阶段 3B 的单能力子切片，不把“有一个离线 Adapter”记为三类真实接口验收。
更完整的 CI、部署硬化、多副本和发布回滚仍属于阶段 6，代表性模型 holdout 另行验收。

## 2. 模块边界与复用

| 模块 | 本轮职责 | 明确不承担 |
| --- | --- | --- |
| `adapters/postal_tracking_contract.py` | 配置、wire schema、一次 JSON 序列化、签名 profile | dotenv、路由、模型、公开 API 字段 |
| `adapters/postal_tracking.py` | 单次查询、错误翻译、记录归属校验、UTC 时间线、隐私投影 | Agent Loop、重试、Fake 回退、会话持久化 |
| `adapters/agent_http.py` | JSON / form 请求、连接池、网络总 deadline、大小限制、HTTP/JSON 错误及熔断 | 邮政字段、业务错误文本解释、业务结果真伪 |
| `TrackingGateway` Port | T2 升级为 `TrackingCommand → TrackingQueryResult`，携带观察元数据 | 供应商字段和 SDK |
| Tool / Result Validator | T2 已完成中立来源、结果措辞及缓存 / 恢复结果校验 | 来源的公开 API / UI 在 T3 收口 |
| LangGraph / API / Web | T2 修正执行作用域及迁移，T3 公开来源 / Web | 供应商 wire 字段、真实业务状态推断 |

供应商字段不进入 Domain；Domain 仍不依赖 HTTPX、供应商协议或 LangGraph。
实际环境尚未开启轨迹，`main.app` 的受控开关已在 T3 实现，Demo 仍使用明确的 Fake。特别不能
仅替换 Demo 的 Fake Gateway 就对外开放：Demo 的鉴权设置不适合真实业务数据。
T1 时 Tool 来源写死为 Fake 的问题已在 T2 修正，公开来源展示在 T3 完成。

T1 不新增依赖，不改变 V1 行为和 V2 OpenAPI。后续组合根需要显式持有 Gateway，并在
lifespan 调用 `close()`；该要求已在 T3 落实，不在每次 Tool 执行时新建或关闭连接池。

## 3. 请求合同

采用 `POST`，表单类型 `application/x-www-form-urlencoded; charset=UTF-8`。
文档所称的消息头字段放在 **表单正文**，不是 HTTP headers，也不放进 URL 查询串。
HTTP `X-Request-ID` 仍可复用本项目请求关联机制，与协议 `serialNo` 分开。

| wire 字段 | 本地来源 / 处理 |
| --- | --- |
| `sendID` / `receiveID` / `msgKind` | 显式配置；不从 URL、用户输入或模型输出推断 |
| `proviceNo` | 保留文档拼写；本地只接受两个 ASCII 数字，默认 `99` |
| `serialNo` | 每次实际查询生成；默认 UUID hex，可注入生成器用于测试 |
| `sendDate` | aware clock 转到显式配置的时区，再格式化为 `YYYYMMDDHHMMSS` |
| `batchNo` | 未配置就不发送 |
| `dataType` | 固定字符串 `1`，仅 JSON |
| `msgBody` | 紧凑 JSON 字符串，仅包含 `traceNo` |
| `dataDigest` | 对同一份 `msgBody` 字符串计算 provisional 兼容签名 |

邮件号保留字符串、大小写和前导零。当前 Domain 允许 1～64 位字母数字；此 Adapter
遵守文档的 30 字符上限，超过上限在网络请求前返回 `invalid_input`。这不等于确认了
邮政的完整业务校验规则，也不把原流程图中的“13 位数字”写死到所有场景。

### 3.1 签名与编码边界

profile ID：`postal-tracking-doc-2019-v1`。

```text
body = 紧凑 JSON，ensure_ascii=False
digest = Base64(raw MD5(UTF8(body + configured signing_system_id)))
form = {消息头字段, msgBody: body, dataDigest: digest}
HTTPX 对 form 进行一次表单编码后发送
```

`body` 只序列化一次，签名输入与表单解码后的 `msgBody` 必须逐字符一致。
不得先 URL encode 再签名，不得把 Base64 中的 `+` 当作空格，也不得把整份表单二次编码。
HTTPX 的 `data=` 与 `json=` / `params=` 分别承担不同编码边界，见
[官方表单编码说明](https://www.python-httpx.org/quickstart/#sending-form-encoded-data)。

**兼容不等于安全认证。** 文档没有消除“原始 MD5 字节还是十六进制文本再 Base64”、
拼接系统 ID 是否等于发送方 ID、是否还有独立共享密钥等歧义。本实现要求单独配置
`signing_system_id`，不推断为 `send_id`；其值用 `SecretStr` 隐藏，但这不会让算法变成
HMAC 或加密。MD5 的密码学限制见 [RFC 6151](https://www.rfc-editor.org/info/rfc6151/)。
若平台禁止 MD5，适配器明确失败，不使用绕过开关，不自动尝试多个签名变体。

fixtures 中两个签名向量由本地合成字符串固定，并用 OpenSSL 交叉计算；它们只能检测
本地字节处理回归，**不是接口方提供的 golden vector**。没有正确签名的猜测式真实试探。

## 4. 响应合同与领域语义

仅接受查询章节的具体 JSON 对象：`receiveID`、严格布尔值 `responseState`、可选
`errorDesc`、可选 `responseItems`。不自动兼容通用章节中的另一种外层包装。
未知扩展字段被丢弃；必填字段、类型与长度不通过则拒绝整个响应。

| wire 字段 | 校验与领域映射 |
| --- | --- |
| `receiveID` | 必填且必须与显式 `expected_response_receive_id` 完全一致 |
| `responseState` | 必须是 JSON boolean；不把字符串 `"true"` 或数字 `1` 强转为成功 |
| `responseItems` | 最多 30 项；成功必须有明确列表，不能把缺失 / null 当作空结果 |
| `traceNo` | 每条记录均须与本次命令完全一致；发现另一单号时拒绝整个响应 |
| `opTime` | 严格 `YYYY-MM-DD HH:MM:SS`、有效日期、显式时区；输出 aware UTC |
| `opCode` | 原样保留到 `event_code`，长度上限 10，不按未确认字典推导状态 |
| `opName` | 校验后用于最新事件标签；不是本地规范化的投递状态枚举 |
| `opDesc` | 校验后做公开文本投影，成为 `description` |
| `opOrgName` | 校验后做公开文本投影，成为 `location` |
| `opOrgCity` / `opOrgCode` | 按文档验证必填及长度，但不透传到当前领域 DTO |
| `opOrgProvName` | 验证可选字段，不新增领域字段 |
| `operatorNo` / `operatorName` | 仅用于本次内存中的已知人员值遮盖，不进入公开结果 |

事件按 UTC 时间稳定升序排列，保留重复及同一时间的多条记录。若最大时间对应多个
不同事件名，标签明确提示查看明细，不任意选一条作为最终状态。附录中的 `10` 与
示例中的 `203` 不自动互换；未知但符合字段约束的操作码原样保留。

配置时区是必要条件。测试使用 `Asia/Shanghai`，不是文档已确认的事实；有夏令时的
时区若出现不存在或歧义的本地时间则拒绝，不猜 offset。

`queried_at` 是本地收到、校验结果后的查询时间，`occurred_at` 是供应方事件时间。
只有最多 30 个节点且没有分页 / 总数口径，因此不能承诺完整历史，也不能把最后一个
返回节点表述为现实中的最终状态。T2 已调整 Tool 的“最新轨迹”措辞和领域来源说明。

### 4.1 失败处理

| 情形 | 领域处理 | 是否允许 Graph 按预算重试 |
| --- | --- | --- |
| 成功且 `responseItems=[]` | T2 返回观察封装且 `data=None`，Tool 映射 `no_match`；仍待供应方确认 | 否 |
| `responseState=false` | `upstream_unavailable` / `postal_tracking_business_rejected` | 否；不分析错误文本猜原因 |
| 空正文、非法 JSON、重复 JSON key、非标准 NaN/Infinity | `contract_violation` | 否 |
| 错误接收方、跨单号、非法字段 / 日期 / 结构 | `contract_violation`，不返回部分事实 | 否 |
| HTTP 401 / 403 | `upstream_unavailable` | 否 |
| HTTP 429 | `upstream_rate_limited`，复用受限 `Retry-After` | 是 |
| HTTP 408 / 504 或网络 deadline | `upstream_timeout` | 是 |
| 其他 HTTP 5xx / TransportError | `upstream_unavailable` | 是 |
| 3xx、其他未约定 HTTP 状态 | `contract_violation`，不跟随重定向 | 否 |
| 适配器未启用 / 平台禁止签名 | 明确 unavailable，不出网 | 否 |

每次 Gateway 执行只尝试一次 HTTP；可重试标志不是适配器自动重试。
未来装配后仍由既有 LangGraph 重试预算决定是否再次执行；这不承诺上游计费 exactly-once。

## 5. 安全与运行边界

- `enabled=False`，只接受显式配置，不从 `.env` 或环境变量自动启用，没有应用注册路径。
- 只允许无凭据、查询串、片段的 HTTPS base URL 和受限相对 path；TLS 校验保持开启。
  真实 endpoint 只能由可信组合根配置，不能由用户或模型传入。这不是任意 URL 的 SSRF 沙箱。
- 共享 HTTP Client 关闭环境代理 / CA 配置自动继承及重定向；默认连接上限 4、响应上限
  1 MiB、网络等待及流式读取总 deadline 5 秒，Adapter 配置最多 30 秒。仍保留 HTTPX
  分阶段 timeout；同步 JSON 解析不等同于可抢占的 CPU deadline。
- 共享层新增的表单互斥校验、严格 JSON、总 deadline 和 `trust_env=False` 也影响其
  DeepSeek 调用方，已纳入全量离线回归。企业代理或私有 CA 后续必须经过显式配置设计，
  不能通过关闭 TLS 验证解决。
- 当前能力级熔断器覆盖 HTTP / 传输 / JSON 解析错误；HTTP 200 的供应商 schema 和业务
  校验发生在其后，**尚未纳入语义级熔断统计**。不能宣称所有契约失败都会累计打开熔断器。
- 只向 Domain 投影允许的字段。原始 `errorDesc`、额外字段和人员字段不透传，校验错误
  不附带原始 payload；不在此模块记录请求正文、签名或业务结果。
- 公开自由文本遮盖响应中已知的人员名字 / 编号及常见手机号 / 邮箱。这是有界防护，
  不是完整 PII 检测；地址、座机和文中未结构化标注的人名仍可能存在，真实展示前需确认
  数据授权、展示范围和保留策略。不能将原始轨迹文本作为高信任模型指令。

## 6. 离线证据与复现

原文档及其业务示例不复制进 Git。测试资料位于
[`tests/fixtures/postal_tracking`](../apps/assistant-api/tests/fixtures/postal_tracking/manifest.json)，
manifest 明确 `synthetic-development`、`provider_verified=false`。

| fixture | 目的 |
| --- | --- |
| `success.json` | 合成单号、逆序事件、保留未统一的操作码、忽略人员字段 |
| `empty.json` | 明确成功且空列表 |
| `business_rejected.json` | 含容易误导的错误描述，验证不会猜成 no_match 或可重试 |
| `cross_mail.json` | 合成跨邮件号事件，验证整批拒绝 |
| `signature_vectors.json` | 固定本地签名向量，含中文与特殊字符编码边界 |

测试还覆盖无效配置、超长 / 前导零 / 国际格式标识、字段类型漂移、超过 30 节点、
同时间冲突、夏令时歧义、敏感 canary 不外泄、HTTP 失败、重定向、平台禁用 MD5，
以及响应头 / 流式正文停滞时的总 deadline。所有出网路径均用 `httpx.MockTransport`。

```bash
# 仅本轮新增用例：84 passed
LANGGRAPH_STRICT_MSGPACK=true .venv/bin/pytest -o addopts='' -q \
  apps/assistant-api/tests/test_phase3b_form_transport.py \
  apps/assistant-api/tests/test_phase3b_postal_tracking.py

# T0/T1 当时为 527，T2 为 585；T3 后当前为 641 passed
LANGGRAPH_STRICT_MSGPACK=true .venv/bin/pytest -o addopts='' -q
```

本轮没有改 Web、OpenAPI 或 Compose，没有重新做浏览器 / Docker 验收；Phase 5E 的
历史证据保持独立，不能作为本 Adapter 已接入 Web 的证明。

## 7. T2 验证与 T3 实施检查点

### T2：先区分查询新鲜度与执行幂等

原收据主键为 `(conversation_id, argument_fingerprint)`。开发前检查已复现：同会话
先查询得到“运输中”，再将 Fake 数据改为“已签收”并以新 turn 查询同单号，仍返回旧值，
Gateway 只被调用一次。这说明原先针对 checkpoint 重放的保护也缓存了用户的新查询。
T2 已通过 query_id、执行 ID 索引、State v3 和兼容迁移修复这一问题，见
[T2 的实现与证据](agent-kernel-phase3b-tracking-t2.md)。

T2 验收要求（代码 / 离线测试已完成；业务熔断补齐经评审放入 T3）：

1. 引入稳定的逻辑查询 / Tool 执行作用域：用户新查询创建新作用域；同一次补槽、重试、
   checkpoint 恢复保持作用域。不直接用每次 HTTP 消息都会变化的 `turn_id` 替代。
2. HTTP 同一幂等键重放返回已有结果；同一逻辑执行从 checkpoint 重放复用收据；同会话
   新查询相同邮件号必须重新执行。参数指纹仍用于一致性检查，不再单独承担新鲜度策略。
3. 覆盖 in-memory / SQLite、State 迁移、旧收据兼容、TTL / 删除和应用重建。
   旧作用域不明的收据不能默认为新查询命中；迁移不得破坏用户已有会话数据。
4. 将 `TrackingTool` 写死的 `fake_gateway` 来源改为可验证、供应商中立的来源合同，
   区分 Fake / 外部接口、协议 profile 与 queried_at，不把原始 wire 数据带进 checkpoint。
5. 增强 Tool 文案、空结果 / 部分历史提示及 Result Validator 回归；评审语义级熔断的
   归属与是否补齐，禁止双重计数。验收时同时保留重放保护与新鲜度证据。

### T3：可控完整路径（已完成本地验收）

复用现有 V2 factory、鉴权 / owner 隔离、readiness 和 Renderer，已建立默认关闭的受控
组合根，明确客户端生命周期。T3 已同步扩展来源 / profile 公开契约、生成类型、Eval
镜像和前端展示，见 [T3 实现证据](agent-kernel-phase3b-tracking-t3.md)。尚无已装配 Adapter 的时限 /
资费保持 unavailable，不偷用 Fake
代替真实依赖；政策和设备价格仍复用现有能力。用合成 Mock 贯通补槽 → 轨迹 →
时间线 → 再次查询，以及超时 / 429 / schema 失败、幂等重放、取消恢复和来源展示。

该工作可复用阶段 6A 的装配准备，但不是跳过 T2 或提前宣称生产发布。

## 8. T4 真实联调前的确认清单

2026-09-09 收尾决定：接口暂不可访问，本清单作为恢复条件保留；本轮不发真实请求，
不要求立即提供凭据。T0～T3 的本地验证不能替代以下确认。

需要接口方或项目负责人确认：

- 可访问的 HTTPS 测试 endpoint / path、网络白名单、TLS 信任链和授权范围；
- 实际 `sendID`、`receiveID`、`msgKind`、省码、流水号格式与唯一性要求；
- 签名拼接材料、编码、MD5 raw / hex 口径和至少一组独立可复算向量；
- 请求字段确实位于 form body，而不是例子中的 URL 查询串；是否存在另一种响应 envelope；
- 响应 `receiveID` 应等于哪方，真实事件码字典、时间格式 / 时区与排序口径；
- `responseState=false` 的稳定业务码与错误分类，空列表 / 缺失列表 / 不存在单号的语义；
- 最多 30 项是否可能截断、是否需要分页，以及流量限额、SLA、可重试范围；
- 可获授权使用的测试邮件号、允许展示的轨迹文本、隐私与保存策略。

这些信息先决定 profile 是否需修订，再进入少量、可计数、明确授权的联调。
凭据只在本地受控配置，不写入聊天、日志、fixture 或仓库。当前无需用户提供 Key。
