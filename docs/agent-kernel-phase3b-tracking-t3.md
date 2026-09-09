# Phase 3B-T / T3：受控装配、公开来源与 Web 闭环

> 日期：2026-09-09。状态：T3 本地实现与 Mock / 浏览器验收完成，并已完成收尾复核。
> 用户确认接口暂不可访问，T4 真实互通暂缓，不计为已完成。
> 新增 56 个 Python 用例，全量 `641 passed`；Web 新增 12 项，全量 `29 passed`。
> 生成类型检查、TypeScript 检查与 production build 通过。未新增依赖、未修改用户 `.env`。
> 本轮没有真实物流请求或付费模型调用，没有提交 / 推送 Git，也没有发布生产服务。

承接 [T2 查询作用域与收据](agent-kernel-phase3b-tracking-t2.md)；T0/T1 的签名、
响应接收方、时区和业务码等 provisional 假设仍然有效，不能用本次 Mock 代替接口方确认。
核心取舍见 [ADR-0013](adr/0013-controlled-tracking-composition.md)。

## 1. 本阶段交付

- `configured_agent.py` 提供受控组合根，复用同一个 V1 政策 / 设备价格 Tool 实例；
  新建资源由 lifespan 管理，支持部分启动失败后的逆序清理。
- `main.app` 仍默认只挂载 V1；显式开启 `ASSISTANT_AGENT_ENABLED` 后才挂载 V2。
  `ASSISTANT_TRACKING_ENABLED` 再独立控制 Postal Gateway；不修改本地实际开关。
- T3 实现时未取得时限 / 资费合同，不注册对应 Gateway；未配置的政策 / 价格也不虚报 available。
  Policy 在补槽前检查能力可用性，不再索取不能执行的查询所需地址、重量。
- JSON、SSE result / done 同步增加来源白名单；OpenAPI、生成的 TS、浏览器解析器和
  独立 Eval schema 一起演进，旧来源缺失响应仍可读取。
- 轨迹熔断成功边界覆盖 HTTP、JSON、wire 契约与领域投影，不再把“HTTP 200”当作
  已取得有效轨迹。Graph 继续独占重试预算，没有嵌套重试或 Fake 回退。

## 2. 装配和生命周期

`create_app` 先解析服务设置，再建立 V1 Registry；开启受控 Agent 时构建依赖工厂。
启动时先初始化 V1 Tool，再进入 Agent 的 `AsyncExitStack`：

1. Postal Gateway（如果启用），拥有独立 HTTP client / 能力熔断器；
2. 可选 Workflow telemetry、可选 Query Understanding model；
3. SQLite checkpointer、Metadata / API / Tool 收据、StateGraph 与 Service；
4. V2 dependencies 和 janitor 调度。

退出时先停止 janitor，再关闭 Agent 拥有的资源，最后由 V1 Registry 关闭借用的 Tool。
测试验证共享 Tool 只初始化 / 关闭一次，V1 和 V2 查询落在同一实例上；SQLite 打开失败
也会关闭已构建的 Gateway。工厂注入的测试 transport 由所属 client 关闭，不跨 lifespan 重用。

这是单进程 SQLite 组合根，不是生产多副本方案。SQLite 父目录必须已存在且受保护；
不自动创建临时库、不静默回退内存库。升级已有库前仍须遵循 T2 的停机 / 一致快照要求。

### 开关与必要配置

示例仅维护在 `apps/assistant-api/.env.example`；未查看凭据或改写用户 `.env`。

| 配置（均带 `ASSISTANT_` 前缀） | 行为 / 要求 |
| --- | --- |
| `AGENT_ENABLED=false` | 默认不挂载 V2，不创建 Agent 数据库 |
| `AGENT_DATABASE_PATH` | 启用时必须显式提供绝对 SQLite 文件路径 |
| `AUTH_ENABLED=true`、`API_KEYS` | 受控 V2 必须有服务鉴权，不能使用无鉴权 Demo 设置 |
| `AGENT_CONVERSATION_TTL_SECONDS=1800` | 服务端 TTL，可配置；Web 本地快照当前仍为 30 分钟 |
| `AGENT_REQUEST_TIMEOUT_SECONDS=30` | Graph 总预算；API 外层多保留 5 秒收尾 |
| `TRACKING_ENABLED=false` | 默认不构建 Postal Gateway；开启须同时启用 Agent |
| `TRACKING_BASE_URL`、`TRACKING_PATH` | 明确 HTTPS endpoint / 相对路径；禁 URL 凭据、查询串和路径逃逸 |
| `TRACKING_SEND_ID`、`TRACKING_RECEIVE_ID`、`TRACKING_MSG_KIND` | 接口方提供；receive 默认 JDPT 只是旧 profile 假设 |
| `TRACKING_SIGNING_SYSTEM_ID` | 独立 SecretStr；不是模型 Key，也不是服务鉴权 Key |
| `TRACKING_EXPECTED_RESPONSE_RECEIVE_ID` | 显式配置，不从请求 sendID 推断 |
| `TRACKING_TIMEZONE`、`TRACKING_PROVINCE_NO` | 明确 IANA 时区；省码默认 99 仍需确认 |
| `TRACKING_PROFILE` | 无默认选择；当前只接受 `postal-tracking-doc-2019-v1` |
| `TRACKING_TIMEOUT_SECONDS=5`、`TRACKING_MAX_RESPONSE_BYTES=1048576` | 单次传输上限；不添加 SDK 内层重试 |

开启但配置缺失 / 非法会在启动前失败关闭，错误不输出输入 URL、身份或签名材料。
不会因为鉴权 Key 存在就自动启用 V2，也不会因物流配置存在就自动打开模型。

`QUERY_MODEL_ENABLED`、`OTEL_ENABLED` 仍各自独立、默认关闭。实际启用模型时，低置信度
自然语言可能被发送给模型供应商；不要把物流开关或本轮授权理解为新的模型调用预算。

### 安全边界

- 不把服务 Key、物流签名材料或 provider URL 放入 `VITE_*`、浏览器存储、Trace 或来源 DTO。
- Web 仍经服务端代理持有服务 Key。当前 owner 绑定服务凭据，不等于完整用户登录体系：
  如果代理给所有访客注入同一个 Key，就不能声称已实现访客间身份隔离。公开部署前必须
  在阶段 6A 明确会话身份映射 / 授权边界，而不是让浏览器提交任意 owner_id。
- 只读轨迹查询仍可能计费或暴露邮件事实；T4 前不填真实 endpoint 后直接启用试跑。
- 本切片不更改 Compose 默认部署；卷、备份、CI、回退和代理身份硬化在 6A 收口。

## 3. 公开来源契约

新增可选 `result.provenance`，当前只投影 tracking，其他意图保持空数组。

```json
{
  "source_type": "external_api",
  "source_name": "postal-tracking",
  "source_profile": "postal-tracking-doc-2019-v1",
  "queried_at": "2026-09-09T02:00:00Z",
  "history_completeness": "unknown"
}
```

以上为合成示意，不是实际请求证据。来源来自 Adapter 配置，不接受供应商 payload 自报
来源，不投影领域 record_id / source_url。它是配置声明，不是认证证明。

- no_match 也有来源 / 观察时间；没有 data 时不编造单号状态或时间线。
- queried_at 是本次取数的观察时间，不等于节点发生时间；同幂等消息重放保留原值，
  新 query_id 重新取数。Gateway 重新取数也不保证上游更新及时。
- profile 缺失不补成当前版本；旧未知来源降为 `unknown / legacy-unknown`。
- Domain `SourceReference` 新增默认 unknown 的完整性声明，旧 State / 收据可按默认值
  解析，无需另升 State v4；`complete` 只表示来源声明，邮政 provisional 始终 unknown。
- Web 校验来源枚举、标识和带时区时间，并对白名单外字段做丢弃；session 恢复同样校验。
  Vue 用文本插值展示节点内容，测试覆盖 HTML 转义；旧快照没有来源时显式提示未记录。

独立文档契约版本为 `0.3.1-phase3b-t3`；SSE envelope 的 `schema_version=1` 不变，
新增字段在 result 内且有兼容默认值。原严格 Eval 客户端必须升级：本轮已同步更新其
镜像 schema，并增加与 OpenAPI / 后端的字段、枚举、默认值对齐测试。

## 4. 熔断与 readiness

Postal Gateway 作为单次尝试的唯一熔断所有者；共享 HTTP client 在此路径禁用内部记账。
其他已有 HTTP 调用仍沿用默认传输层熔断。每次尝试只记一次失败，完整校验后才记成功。

计入：timeout、transport、429、5xx、拒绝访问、非法 JSON / wire schema、接收方不符、
跨单号、非法时间，以及无稳定业务码的 responseState=false。有效空结果算成功。
不计入：发请求前的流水号 / 签名配置错误、本地时钟内部错误、取消、熔断拒绝本身。
取消和本地错误释放半开探测位，避免持续卡在 probe_in_flight；状态记账使用 shield 收尾。

默认阈值 3 次连续失败、恢复窗口 30 秒是本地策略，不是供应商 SLA。契约失败与未知业务
拒绝不重试；瞬态错误至多两次实际请求，受 Graph 总 deadline / retry budget 限制。

readiness 仅读取 SQLite、本地 Tool 状态与熔断快照，不发物流或模型探测请求：

- 能力注册表示配置可用，`capabilities.available` 不代表供应商当前在线；
- tracking 熔断时对应检查为 degraded；没有任何 ready 能力时返回 503；
- 其他能力仍 ready 时可返回 degraded，故障隔离不关闭全部业务；
- 只投影固定 capability 名和粗粒度状态，不公开 URL、路径或凭据。

V2 使用 `/v2/agent/health/ready`；原 `/health/ready` 仍是 V1 依赖检查，不能在只装配
轨迹时用 V1 的检查结果代替 Agent readiness。Compose 探针切换留给部署切片处理。

## 5. 验收证据

| 范围 | 本轮证据 |
| --- | --- |
| 受控组合根 | 30 项：配置 / 默认关闭、共享 V1 生命周期、JSON/SSE、新查询 / 重放、重启续聊、owner / 删除、未开放能力、故障矩阵、readiness |
| 语义级熔断 | 15 项：11 种失败单次记账、半开恢复 / 取消、空结果成功、局部错误不计数 |
| 公开契约 | 11 项：来源白名单、旧来源、非法字段、后端 / Eval / OpenAPI 对齐 |
| Web | 新增 12 项：来源和空结果渲染、旧快照、partial、HTML 转义、JSON/SSE 非法来源、恢复校验 |

```bash
LANGGRAPH_STRICT_MSGPACK=true .venv/bin/pytest -o addopts='' -q
# 641 passed（本轮前 585）
cd apps/chat-web
npm test
# 29 passed（本轮前 17）
npm run check:agent-types
npm run build
```

浏览器使用真实 Web → V2 → LangGraph → SQLite → Postal Gateway → MockTransport，
只有本机 HTTP，邮政外部响应全部合成。已验证能力禁用、缺邮件号补槽、刷新恢复、时间线、
来源 / profile / 完整性、同单号新查询变化、待补槽取消、空结果和 429 的无伪造结果展示，以及停止
读取后通过“安全重试”完成响应。浏览器中止只停止读取，不保证取消服务端执行；完成
收据的免调用重放另由 JSON/SSE 集成测试证明，不把任何取消时机都宣称为零追加请求。

可复现的测试入口：`apps/assistant-api/tests/tracking_browser_fixture.py`，不是生产入口。

```bash
# 仓库根目录；这是测试目录中的临时库，不指向用户会话库。
tracking_smoke_dir=$(mktemp -d)
SPB_TRACKING_SMOKE_DB="$tracking_smoke_dir/agent.db" LANGGRAPH_STRICT_MSGPACK=true \
  .venv/bin/uvicorn --app-dir apps/assistant-api/tests \
  tracking_browser_fixture:create_fixture_app --factory \
  --host 127.0.0.1 --port 18083 --no-access-log

# 另一个终端，apps/chat-web 目录：
VITE_ASSISTANT_UI_MODE=agent CHAT_WEB_ASSISTANT_API_URL=http://127.0.0.1:18083 \
  CHAT_WEB_ASSISTANT_API_KEY=synthetic-browser-only npm run dev -- \
  --host 127.0.0.1 --port 4303 --strictPort
```

合成单号：`1234567890123` 首次运输 / 再查妥投，`0000000000000` 空结果，
`1111111111111` 429，`2222222222222` schema 失败，`3333333333333` 延迟 10 秒用于
停止读取 / 安全重试。不要将这些测试单号发给真实供应商。测试服务只监听 loopback，
退出后保留临时库供复核，不修改任何已有用户库。

本次验收结束后已关闭页面和两个 loopback 测试服务；临时库位于
`/private/tmp/spb-tracking-t3.IOpqLN/qa.db`，只含上述合成查询，可用于本机复核，不作为生产数据迁移证据。

## 6. 收尾复核与下一步顺序

2026-09-09，用户明确各接口暂时无法真实访问。本阶段按“本地 / Mock 工程验收完成”
收尾，T4 转为外部依赖暂缓，不继续探测 endpoint 或请求物流凭据，不据此宣称生产验收。

本次重新执行：

- 完整 Python 工作区：`641 passed in 10.73s`；
- Web：6 个文件、`29 passed`；`check:agent-types`、`vue-tsc` 通过；
- 默认模式与显式 `VITE_ASSISTANT_UI_MODE=agent` 的 production build 均通过；
- `git diff --check` 通过。

本轮没有应用代码变更，也未重跑浏览器交互；浏览器证据仍指第 5 节的 T3 验收。
未编辑用户 `.env`、未打开物流开关、未新增真实物流或付费模型调用。本次收尾不包括
Git 提交 / 推送：工作区保留 Phase 5E 和 T0～T3 等既有未提交改动，HEAD 仍为 `559161f`。
测试通过不等于这些变更已经进入远程或部署环境。

新的《资费查询接口规范》已收到，并完成
[Phase 3B-P 文档评审](agent-kernel-phase3b-postage-analysis.md)；资费 Adapter 尚未实现，
时限文档仍未提供。后续顺序调整为：

1. 本轮完成资费合同差距分析；后续开发可从 P1 领域 / 产品 / 地区 / 报价口径与离线
   fixtures 开始，再进入 P2 协议 Gateway、P3 受控集成。未确认项仍需 provisional 标记。
2. 不依赖真实接口的 6A 单实例卷 / 备份、CI、Web 开关、代理 owner 隔离与回退可独立推进。
3. 网络恢复后再恢复 T4：补齐 endpoint / 许可、签名独立向量、接收方、时区 / 事件码、
   空结果 / 完整性、稳定错误码和授权测试单号，修订 profile；另获明确次数 / 范围授权后
   才进行可计数真实联调。资费 P4 也单独验收，不共用轨迹协议或凭据。
4. 独立 holdout 的人工审核 / 预算仍是另外一条验收线，不把物流 Mock 或浏览器通过当作
   理解模型质量提升证据。
