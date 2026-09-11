# 商品价格公开协议与协调发布（D4～D7）

2026-09-11。实现范围为设备和已有生鲜来源，不代表任意商品覆盖。
阶段进度、远程 CI 和实际上线证据以[状态页](../../../../docs/current-status.md)为准。

## 1. 装配与公开边界

显式选择 `ASSISTANT_PRICE_DATA_MODEL=catalog_v2` 后，应用持有唯一 V2 Repository / Query Service：
Agent 使用 `product_price`，保留的 V1 `device_price` HTTP 包装器也借用该 Service。
Agent 不同时注册两个价格意图；缺少 DSN 时商品能力不可用，不构造旧 SQL 作为 fallback。
默认配置仍保留 `device_v1` 供独立旧环境使用；完整移除旧实现属于 E 的后续收口。

内部 ProductPriceData / 读取事实绝不直接发送给浏览器。API 白名单只输出：

- identity：设备品牌、产品、规格，或生鲜品名、来源规格、市场名称；
- quote：金额字符串、原价类型、无金额状态、口径、原始/标准计价单位、观察时间及精度；
- source / region：来源名称、profile、无凭据 HTTP(S) URL、报价地区；
- queried_at / freshness：真实读取时点与来源级时效状态；未配置阈值仍为 unknown；
- last_known_price：仅在能够证明时单列的历史补充；当前 SQL 保守不返回，不补作现价。

不输出数据库 ID、revision/match、查询指纹或候选引用。设备属性不得覆盖已证明规格字段。
公开金额不使用浮点数，500g→kg 仅做十进制精确换算；不跨来源合并均价，不换算未知商品重量。
设备 PACKAGE_TOTAL 可以没有标准单位价；生鲜有价结果必须带匹配的标准单位。

OpenAPI、生成 TypeScript、Web 运行时校验和 Eval 独立模型同时维护。
可运行 `scripts/update-product-price-openapi.py` 更新新增价格 schemas，再执行 Web 的
`generate:agent-types` / `check:agent-types`。其余人工维护的物流协议说明保持原样。

## 2. 候选请求与幂等

`required_inputs` 的 `price_selection` 字段提供 `price_candidates`：
每项只有 `candidate_token`、可展示 label、带时区 expires_at。choices 仅为兼容性标签，不能承载 JSON 身份。
Web 点击后发送：

```json
{
  "conversation_id": "已有会话 UUID",
  "price_selection": {"candidate_token": "服务端返回的短期令牌"},
  "stream": true
}
```

令牌必须是 43 字符 base64url，不允许携带 message、explicit_intent 或 confirm_overwrite，不能创建会话。
选择包含在消息参数指纹中；改变 token 后复用幂等键返回冲突。未使用该字段的旧请求指纹字节保持不变。
owner 来自受信服务身份或签名访客 Cookie，不从 body/header 自报；运行期检查候选作用域与绝对 TTL。
同一请求的 JSON/SSE 共用持久化结果；API 收据先于状态迁移检查，因此已完成的合法旧收据仍可免执行重放。
新 query 重读价格；恢复、重复提交、读取快照均不能刷新候选的期限或绕过预算。

## 3. 客户端与状态版本协调

catalog 模式下浏览器必须发送 `X-Agent-Contract: product-price-v1`；能力目录、消息和快照都检查。
不匹配时在 SSE 和状态写入前返回 HTTP 409 `agent_client_upgrade_required`，提示刷新页面。
这不是认证凭据，只是兼容性门禁；服务密钥身份和 V1 保持原认证方式。
catalog 模式的新请求不能显式选择旧 `device_price`，返回 409 `agent_price_intent_upgraded`；旧结果仍可读取。

State 当前为 **4**。1/2 的原有非价格增量迁移继续生效，3→4 保留 query/call 身份和原事实。
1/2/3 的未完成设备价格（或隔离开发商品价格）状态返回非重试型 `price_query_restart_required`，
不猜测新的槽位、候选或执行身份。已完成的旧价格结果保留 `device_price` 和旧 Renderer。

新增 `GET /v2/agent/conversations/{conversation_id}`：在同一会话锁和 owner/TTL 校验后读取持久化停止点，
不运行 Graph、工具或模型，不延長 TTL；执行中返回冲突。Browser 先核验身份，再用它恢复可继续的状态。
存在未确认请求时保留原幂等键供用户手动重试，不用较早快照覆盖新请求，也不自动重发。
过期/不存在/旧价格 pending 时保留本机历史，清理不可继续的输入并要求新查询。

备份 profile 升为 `agent-state-v4-receipt-v2-snapshot-v1`；新工具可以读原 v3 profile，
仍核对 LangGraph 依赖版本、表结构、完整性和未完成 claim。旧镜像不能打开新状态库。
回滚使用发布前的旧镜像、旧配置和旧状态副本，不在新库上降级 schema。

## 4. UI 与评测验收

通用价格 Renderer 显示设备规格/当前配置价、原价类型、无金额状态、生鲜批零口径、
元/500克与元/公斤、来源、地区、数据日和查询时点。DAY 按北京时间显示来源日，unknown 时效不显示实时价。
候选按钮使用独立结构化输入，过期按钮不可选，文本由 Vue 转义；本机历史亦经协议校验。

新增 7 条 / 9 轮合成 public development，经真实 API / SQLite / LangGraph 与独立 Eval 执行，
含候选→选择、直接设备报价、生鲜双单位、今日只有旧数据、地区/口径补槽、苹果歧义和物流不可用。
此数据集不作为 holdout，也不能证明真实模型质量或真实数据覆盖。默认 CI 同时运行原资费和商品 Eval。
另有 JSON/SSE/重放、双访客、SQLite 重启、非法 DTO/token、旧状态拒绝及 Web 卡片/历史负向测试。

## 5. 限定内网发布顺序

1. 全量离线测试、两版隔离 MySQL、Web 类型生成/校验/双模式构建、合成部署演练通过。
2. 提交并安全推送；核实匹配提交的远程 CI，不能把上一版绿色状态作为本版证据。
3. 核对原 API/Web 容器、挂载、Origin、价格/RAG 目标；只在私有目录保存配置与恢复信息。
4. 构建不可变 amd64 产物。隔离新状态先验证线程/SQL/模型 profile 和依赖可用，不占用原入口。
5. 停止原 Agent Web/API 写入方，整库备份并 verify，恢复到新目录；不动其他容器或业务数据。
6. 新 API/Web 配套启动，沿用原签名密钥、HTTP 内网例外、网络与资源保护。
7. 核验政策、设备/生鲜、补槽、候选、JSON/SSE、双访客与正常物流不可用后，记录镜像摘要与入口。
8. 保留旧容器/状态/配置及回滚脚本；扩大来源、最小权限账号、历史 SQL 与旧实现清理继续按 PRICE-G 推进。

本文件不包含账号、凭据或自动操作公司数据的脚本。部署授权不包含更改来源数据、扩大权限或升级共享 daemon。
