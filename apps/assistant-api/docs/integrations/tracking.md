# 邮政轨迹接口：当前适配与确认清单

[Assistant API](../../README.md) · [项目文档导航](../../../../docs/README.md)

状态汇总见[当前状态](../../../../docs/current-status.md)。T0～T3 已完成本地 / Mock 通路，
T4 真实互通未验，按本轮收口约定作为遗留保留、暂不推进；本文保留重新启动时所需的合同，不是开发队列。

## 1. 实现边界

- [wire contract](../../src/spb_assistant_api/adapters/postal_tracking_contract.py)：
  显式配置、序列化、签名和字段验证。
- [Gateway](../../src/spb_assistant_api/adapters/postal_tracking.py)：
  单次查询、响应归属/时间线/隐私投影、语义熔断；不拥有重试 Loop。
- `configured_agent.py` 管理开关/生命周期；Tool/Validator、SQLite、V2、Web 已贯通。
- 正式缺配置时 unavailable，不回退 Fake。合成 fixture 不得发送给供应商。

## 2. 具名临时请求合同

profile：`postal-tracking-doc-2019-v1`。它是对资料歧义的明确工程选择，不是接口方确认。

POST form：`application/x-www-form-urlencoded; charset=UTF-8`。
消息头字段位于表单正文，不是 HTTP header，不放入 URL 查询串。

| 字段 | 处理 |
| --- | --- |
| sendID / receiveID / msgKind | 显式配置，不能从 URL/用户输入推断 |
| proviceNo | 保留文档拼写，两位 ASCII 数字；默认 99 待确认 |
| serialNo | 每次实际尝试生成，默认 UUID hex；不同于业务 tool_call_id |
| sendDate | aware clock 按配置时区转 YYYYMMDDHHMMSS |
| batchNo | 未配置不发送 |
| dataType | 固定字符串 1，仅 JSON |
| msgBody | 只含 traceNo 的紧凑 JSON 字符串 |
| dataDigest | Base64(raw MD5(UTF8(msgBody + signing_system_id))) |

只序列化一次 JSON，签名与传输使用相同字符串，HTTPX 最后只做一次表单编码；
不预先 URL encode，不丢失 Base64 的加号。signing_system_id 独立配置，不推断为 sendID。
平台禁止 MD5 时失败关闭；兼容算法不是 HMAC 或加密，不自动尝试签名变体。

领域邮件号保留字符串/前导零，允许受限字母数字；Adapter 遵循资料的 30 字符上限。
当前用户入口提示 13 位数字，但不能把该提示视为完整供应商号码规范。

## 3. 响应与事实校验

仅接受选定查询 envelope：receiveID、严格 boolean responseState、可选 errorDesc、
responseItems。拒绝重复键、NaN/Infinity、错误结构；不递归尝试其他包装。

| 内容 | 校验 / 映射 |
| --- | --- |
| receiveID | 与显式 expected_response_receive_id 一致 |
| 成功 responseItems | 必须是列表，最多 30；缺失/null 不当作空列表 |
| 每项 traceNo | 与本次命令完全一致；跨单号整批拒绝 |
| opTime | 有效 YYYY-MM-DD HH:mm:ss + 明确 IANA 时区，转换 aware UTC |
| opCode / opName | 保留合法码及事件标签，不猜字典或业务终态 |
| opDesc / opOrgName | 白名单文本投影为描述/位置 |
| opOrgCity / opOrgCode | 校验但不向当前 DTO 透传 |
| operatorNo / operatorName | 不公开；用于有界遮盖已知人员值 |

时间线稳定升序，保留同刻多事件/重复记录；最大时间事件名冲突时不任意指定最终状态。
DST 歧义/不存在时间拒绝。事件 occurred_at 与本次观察 queried_at 分开。

Gateway 返回 `TrackingQueryResult(data, source, queried_at, history_completeness)`；
空结果同样带来源。当前 profile 完整性始终 unknown，最多 30 条不能证明完整历史。
来源来自可信 Adapter 配置，不接受 payload 自报；external_api 标签在 Mock 中也不是互通证据。

公开 `result.provenance` 只含来源类型/名称/profile、观察时间和完整性。
新查询刷新，已完成消息/工具重放保留原观察时间；no_match 不代表邮件不存在。
自由文本遮盖不是完整 PII 检测，真实开放前仍须确认展示和保留权限。

## 4. 失败与熔断

- timeout、429、5xx/transport 明确映射瞬态故障；图按预算最多两次实际尝试。
- 401/403、responseState=false、错接收方/单号、非法 schema/日期不靠文本猜重试。
- 有效成功空列表是 no_match；无稳定业务码的拒绝不是 no_match。
- Gateway 完整校验后才记成功；传输/JSON/业务合同失败一次记账，不双层累计。
- 默认 3 次连续失败、30 秒恢复窗口是本地策略，不是供应商 SLA。
- 取消或本地配置/时钟错误释放半开探测位，不误计上游失败。

## 5. 启用条件与配置

配置唯一示例为 [Assistant .env.example](../../.env.example)，
`ASSISTANT_AGENT_ENABLED` 与 `ASSISTANT_TRACKING_ENABLED` 分开、默认关闭。
必须有服务鉴权、独立 SQLite、明确 HTTPS endpoint/path、身份、签名材料、
响应接收方、时区和显式 profile；配置非法时启动前失败。

HTTP 连接池复用；默认单次 5 秒/1 MiB，无重定向、环境代理继承或 TLS 降级。
地址只能由可信部署配置提供，不能接受用户任意 URL。Readiness 不发送真实轨迹探测。

## 6. T4 关闭缺口所需证据

| 确认项 | 必须取得的材料 |
| --- | --- |
| 接入 | HTTPS endpoint/path、网络白名单、TLS 信任链、权限与批准测试范围 |
| 身份/流水 | sendID/receiveID/msgKind/省码、serialNo 格式及唯一性 |
| 签名 | raw/hex、拼接材料、字符编码、独立向量（含特殊字符） |
| 请求/响应 | form 放置、完整脱敏 HTTP 样例、envelope、响应接收方 |
| 事件 | 事件码字典、时区/时间格式、同刻排序、当前状态语义 |
| 空结果/错误 | 空/缺失列表、单号不存在、稳定拒绝码及可重试范围 |
| 覆盖/容量 | 30 条截断/分页、QPS、超时、SLA、查询计费/副作用 |
| 隐私 | 授权测试单号、文本字段、可展示范围与保存策略 |

先修订/确认 profile 并回归，再获单独调用授权进行可计数真实联调。
凭据只进入受控配置，不写聊天、Git、日志或 fixture。

## 7. 可复核资产

- [合成 fixture manifest](../../tests/fixtures/postal_tracking/manifest.json)，
  本地签名向量不是供应商 golden vector。
- `test_phase3b_*` 覆盖 wire、跨单号/时间、执行作用域迁移、来源、语义熔断和受控组合根。
- `tests/tracking_browser_fixture.py` 为本机 Mock 页面夹具，不是生产入口。
- 查询新鲜度取舍见 [ADR-0012](../../../../docs/adr/0012-query-scoped-tool-receipts.md)，
  装配见 [ADR-0013](../../../../docs/adr/0013-controlled-tracking-composition.md)。
