# Assistant 服务运维

[服务入口](../README.md) · [跨服务运维与发布交接](../../../docs/operations.md)

本页只维护本服务的配置语义、身份/状态库不变量、故障定位和观测行为。
Web/代理/入口/镜像配套与私有发布清单归根运维文档；具体部署命令唯一维护在
[deploy/agent/README](../../../deploy/agent/README.md)。

## 1. 配置来源与必需条件

普通 `AssistantSettings` 的 dotenv 优先级见[服务入口](../README.md)；
五能力 Demo 必须使用[本地开发](local-development.md)给出的隔离参数。
`offline_postage / deployment_demo` 使用显式合成配置；
`deployed_app` 只读进程/显式配置、不读取 dotenv，必须服务鉴权、单 worker、
managed SQLite 和浏览器身份。没有获准依赖时不要注入 Fake，至少一项能力 ready 才可接流量。

| 配置组 | 关键项 | 校验 |
| --- | --- | --- |
| Agent（ASSISTANT_ 前缀） | AGENT_ENABLED、AGENT_DATABASE_PATH、AGENT_MANAGED_STORAGE_ENABLED | 绝对受控路径，固定 agent.db |
| 服务鉴权 | AUTH_ENABLED、API_KEYS | 有效白名单；代理 Key 必须在其中 |
| 浏览器 | AGENT_BROWSER_SESSION_ENABLED、PROXY_API_KEY、SIGNING_KEY、PUBLIC_ORIGIN | 后三项同样带 AGENT_BROWSER_ 前缀；签名 Key 不复用服务 Key |
| 依赖 | RAG_BASE_URL/RAG_API_KEY、MYSQL_DSN | 只读、明确授权；未配置能力不装配 |
| 预算 | AGENT_REQUEST_TIMEOUT_SECONDS、各上游 timeout | Agent/代理总预算须覆盖既有政策链路，不延长模型/物流单次预算 |
| 可选 | QUERY_MODEL_*、TRACKING_*、OTEL_* | 各自开关、预算/合同/访问许可独立 |

完整字段见 [Assistant 示例](../.env.example)、
[部署示例](../../../deploy/agent/.env.example)与 `DeploymentSettings`。
服务 Key/DSN/证书/签名 Key 不入 Git；配置文件最小权限。不要输出完整 docker inspect、
compose config 或 nginx -T，它们可能包含秘密。

## 2. 入口与匿名身份

默认 HTTPS、精确 Host/Origin、`__Host-spb-agent` Secure/HttpOnly/SameSite=strict Cookie。
仅 Web 公开必要路由，后端无 host port；metrics/ready/调试限制运维网络。
代理 Key 控制服务准入和共享配额，Cookie 决定会话 owner，session_ref 检测旧页面身份漂移。
随机访客不是登录、租户或客户报价资格。

签名轮换可保留一个前代 Key；最后旧 Key 签发后的最大绝对 TTL 到期才移除。
紧急删除旧 Key 会使相关身份失效，不是个体撤销。代理角色 Key 仅一个，需协调前后端切换。
恢复状态库时必须另行匹配 Origin/签名/服务配置；备份本身不包含秘密。

HTTPS 是默认边界。已获准的私有 HTTP 例外必须与 Web/代理成组配置，且不提供传输加密；
完整开关、适用范围和旧主机兼容入口统一见
[跨服务入口安全](../../../docs/operations.md#入口安全与兼容)，不在本页维护第二套发布参数。

## 3. 停服整库备份与恢复

只支持单实例 POSIX 本地文件系统。managed 目录为当前运行 UID/0700，数据库/锁文件为 0600。
拒绝相对路径、链接、硬链接、错误 UID/权限和宽泛目录；不自动修复旧目录或收编未标记旧库。
租约覆盖所有连接，第二个合作进程开库或运行中备份立即失败；锁文件不能删除。

profile `agent-state-v3-receipt-v2-snapshot-v1` 包含：

| 内容 | 表 |
| --- | --- |
| owner/状态/TTL | agent_conversations |
| 创建幂等 | agent_conversation_creation_receipts |
| 消息 claim/完成响应 | agent_idempotency_receipts |
| 历史/当前工具收据 | agent_tool_execution_receipts、agent_tool_execution_receipts_v2 |
| 迁移标记 | agent_persistence_migrations |
| LangGraph | checkpoints、writes |

操作顺序：确认写入方已停 → 获取租约 → 拒绝未完成 claim → SQLite Backup API →
integrity/schema/摘要/版本校验 → 发布清单 → 恢复到全新目录 → 隔离验收后切换。
不可只复制正在写的 agent.db，WAL 同样属于持久状态。
快照清单保存依赖版本，恢复不能顺便升级 LangGraph/迁移 schema。

CLI：`python -m spb_assistant_api.storage_cli init|backup|verify|restore`。
参数和不覆盖旧目录的分步命令见[部署手册](../../../deploy/agent/README.md)；
无网络独立演练见[存储演练](../../../deploy/storage/README.md)。
成功退出 0，存储错误退出 2；失败半成品保留，不自动删除或重试覆盖。

| 错误/操作 | 处理 |
| --- | --- |
| store_busy | 先确认所有写入进程停止，不删锁文件 |
| unfinished_requests | 核对中断请求和收据，不删除 claim 强行继续 |
| target_exists | 换全新目标，保留原目录 |
| schema/版本/摘要不符 | 停止恢复，使用匹配镜像/可信快照，不跳过检查 |
| 恢复后验证 | 原 owner、绝对 TTL、完成重放、暂停继续、越权拒绝和删除 |
| V1 模式回退 | 同步切 API/Web，Agent 库不打开/删除/降级；不等于跨版本回滚 |

切到 restored 卷后，下一次备份源必须改成实际运行库。
备份可能含用户输入/结果，0600 不等于加密、SHA-256 不等于签名；
不接收不可信快照。恢复可能带回备份后已删除的数据，目前没有备份外删除账本。

## 4. 排障：分清可用性、数据与答案

| 现象 | 检查顺序 |
| --- | --- |
| /v2 路由不存在 | 是否启动 V1 默认入口，Web 构建模式是否匹配 |
| 401/403/409 身份错误 | 代理角色 Key、精确 Origin、Cookie/TTL、session_ref；不禁鉴权“修复” |
| readiness 503 | Agent/持久化/checkpoint 表、是否至少一项能力 ready |
| readiness degraded | janitor、单项依赖/熔断；不把局部故障升级为全部不可用 |
| 价格 no_match | 实际表/关联/当前型号与规格覆盖；SELECT 1 只证明连接 |
| 政策 no_context / reranker_rejected / llm_rejected | 分别定位召回、相关性、证据充分性，不一律调低阈值 |
| 三项物流 unavailable | 按状态页核对是否本就未装配，不开启 Fake 或索取无用槽位 |
| SSE 中断/超时 | 代理读取预算、终止事件和原幂等请求；不要自动换键 |
| 恢复显示旧结果 | 区分消息重放与主动新查询，核对 query_id/观察时间 |

`/health/ready` 是 V1 两工具检查；Agent 用 `/v2/agent/health/ready`。
后者检查本地持久化、janitor、能力状态，至少一项可用即可；不做模型/物流付费探测。
available/readiness 都不保证特定型号有价、文档能回答或供应商此刻成功。

## 5. 观测边界

API run 包含幂等重放；workflow invocation / Node 指标只统计实际执行。
语义 Trace 解释路径，Node span 记录实际 await/执行耗时，interrupt 不包含人类等待。
标签只用固定意图/状态/节点；不放问题、单号、参数、结果、Key、原始会话 ID 或异常正文。

OTel 默认关闭，显式启用后由组合根管理 provider、采样与有界异步导出；
采样不保证保留所有错误，队列满可丢遥测，不保证跨服务传播或硬 kill 零丢失。

Agent + Grafana / Prometheus / Tempo 的启动、端口和停止方式归
[本地合成监控栈](../../../docs/operations.md#本地合成监控栈)；生产监控不是该演示栈的默认延伸。
全局部署状态见[当前状态](../../../docs/current-status.md)，后续能力见[路线图](../../../docs/roadmap.md)。
