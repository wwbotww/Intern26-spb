# Phase 6A-1：代理服务鉴权与浏览器访客隔离

> 状态：本地 / 合成验证完成；默认关闭。2026-09-09。
> 本轮不启用真实环境、不访问模型或物流接口、不提交或推送 Git。
> 完整阶段 6 仍为 In progress；本切片不替代登录认证、生产部署或多副本验收。

## 1. 修复的边界

Nginx 原本为所有浏览器注入同一个 Bearer Key，后端又把服务 Key 哈希作为 owner，
因此经该代理访问的访客共享会话归属。现在对一个明确配置的代理 Key 增加匿名访客身份，
复用既有会话读取/恢复/删除及幂等归属检查，不修改 Graph 的业务路由。

| 标识 | 来源与可信边界 | 用途 |
| --- | --- | --- |
| 代理服务 Key / client_id | 后端白名单鉴权，Nginx/Vite 服务端注入 | 服务访问、共享限流；不代表浏览器访客 |
| Cookie / agent_owner_id | 后端随机 ID + origin-bound HMAC；HttpOnly Cookie | 访客的 conversation / 幂等归属；不代表账号或业务权限 |
| session_ref / X-Agent-Session | Cookie 校验后的非敏感引用，JS 内存持有 | 检测旧页面身份漂移；单独持有不能通过鉴权 |
| conversation_id / thread_id | 既有服务端会话与 LangGraph 标识 | 查找状态；不能充当授权凭据 |

设计理由、无状态权衡和安全参考见 [ADR 0017](adr/0017-browser-visitor-identity.md)。

## 2. 已实现

- `security/browser_session.py`：版本化签名、256-bit 随机 ID、绝对 TTL、当前/前代
  签名 Key、origin 绑定、Cookie 长度/格式/重复/篡改/过期校验；密钥和 Token 不入 repr。
- `OperationsMiddleware`：先服务鉴权与共享配额，再代理角色 / Origin / Cookie /
  session_ref 校验；`client_id` 不替换为访客值。调用方伪造的 owner header 不作为身份。
- 新增条件挂载的 bootstrap API、严格 `reset: boolean` schema、no-store 响应。
  普通核验不发送 Set-Cookie，避免旧页面延迟响应覆盖新身份；重建不自动删除旧会话。
- 原 JSON/SSE/删除均使用解析后的 owner。其他可信服务 Key 保持 key-scoped owner；
  原代理 Key 的旧会话不迁移到新浏览器身份。没有数据库或 Graph State schema 迁移。
- Web 启用后先 bootstrap，匹配本地 snapshot 的 session_ref 才展示历史或恢复待重试键。
  到期/身份错误锁定界面；返回标签页时重新核验，跨标签页绑定变化时清空旧展示。
  中止旧流并隔离延迟事件，不把旧 pending 请求自动换身份重发。
- OpenAPI `0.3.3-phase6a1` 与生成 TS 同步；`.env.example` 增加默认关闭配置。
  Nginx/Vite 清除伪造身份头；Docker Web 增加非敏感构建开关，排除 `.env.*`。
  Docker 模板调整未代替实际容器/TLS 验收，默认 Compose 仍为 V1。

## 3. 配置与运维语义

后端启用需同时满足 `ASSISTANT_AGENT_ENABLED=true`、鉴权及绝对 SQLite 路径。
新增配置（`ASSISTANT_` 前缀）：

| 配置后缀 | 默认 / 要求 |
| --- | --- |
| AGENT_BROWSER_SESSION_ENABLED | false |
| AGENT_BROWSER_PROXY_API_KEY | 必须是 API_KEYS 的一项，且与 Web 代理持有的 Key 相同 |
| AGENT_BROWSER_SIGNING_KEY | 独立随机密钥，32～512 字节；不能复用任何 API Key |
| AGENT_BROWSER_PREVIOUS_SIGNING_KEY | 默认空；可暂留一个前代签名 Key |
| AGENT_BROWSER_PUBLIC_ORIGIN | 精确 origin，例如 https://agent.example；无路径、尾斜线、凭据或查询参数 |
| AGENT_BROWSER_COOKIE_SECURE | true；false 只允许明确的本机 HTTP origin |
| AGENT_BROWSER_SESSION_TTL_SECONDS | 1800；范围 60～86400 秒，固定绝对期限 |

前端同时设置 `VITE_ASSISTANT_UI_MODE=agent` 和 `VITE_AGENT_BROWSER_SESSION=true`。
两者都是构建时开关；修改容器运行时环境不会改变已打包 JS。浏览器不能持有代理或签名
密钥；实际 Compose 尚未映射这些配置/构建参数，不能仅改根 `.env` 就声称已部署。
本轮只更新示例文件，没有读取或修改真实 `.env`。

轮换策略：部署新签名 Key 并保留一个前代 Key，旧身份和绝对到期时间保持不变；所有
新签发 Cookie 使用新 Key。等最后一次旧 Key 签发后的最大 TTL 结束再移除前代 Key。
若泄漏，立即移除相应 Key 会使受影响会话失败；这不是精细撤销或无感轮换。代理 Key
角色本轮只配置一个，轮换需协调 Web/后端切换；不提供双代理角色宽限期。

| 情况 | 对外行为 / 后续动作 |
| --- | --- |
| 无 Cookie | 业务接口 401；bootstrap 可建立新访客，不继承旧 snapshot |
| Cookie 无效/过期 | 401；显式 reset 才替换；浏览器已自动清除 Cookie 时按“无 Cookie”处理 |
| Origin 缺失/不匹配或跨站 Fetch Metadata | 403；修正同源入口，不以 Referer/null 自动放行 |
| 重复安全头 | 400；重复同名身份 Cookie 为 401 |
| session_ref 缺失/变化 | 409；UI 停止旧请求并重新核验，不能自动改 owner 重发 |
| 访客 B 恢复/删除 A 的会话 | JSON/DELETE 404；SSE 的 error 事件携带 404，不发 result/done |
| 重建访客 | 新随机身份 + 清除本地绑定；旧 Token 未撤销，旧服务端状态按 TTL 清理 |
| 服务 Key 无效 / 配额耗尽 | 401 / 429；重建身份不能绕过同一代理的共享配额 |

## 4. 验证证据与复跑

本轮新增 54 项 Python、15 项 Web 测试；该切片完成时全量 Python 976、Web 70 项。
类型检查、OpenAPI 生成一致性、Agent/Legacy 隔离构建通过。产物扫描未发现测试代理 Key
或签名 Key。未执行真实模型/物流请求，不把工程回归数量写成意图理解准确率。

Python 覆盖两个独立 cookie jar、相同创建幂等键、跨 owner JSON/SSE/删除、伪造头、
Schema/Origin/签名/过期/重复 Cookie、SQLite 重启恢复、签名轮换、共享限流、旧核验
响应不覆盖新 Cookie、默认关闭与公开契约。Web 覆盖核验前不发请求、身份绑定存储、
TTL、显式 reset、不自动重发、遗留模式兼容。

Browser 技能用于真实页面验收：

1. 仅合成资费可用；发起询价，停在命令绑定确认。
2. 刷新后先核验再恢复同一会话，确认得到 CNY 12.30 合成报价与原观察时间。
3. 第二标签页可恢复同浏览器身份；重建身份后两页均无旧会话/报价/待重试记录。
4. 初测发现普通核验 Set-Cookie 的覆盖竞态，修复并新增可重复的延迟响应回归；复测通过。

从仓库根目录运行（无需真实 Key）：

```bash
.venv/bin/pytest -o addopts='' -q
npm --prefix apps/chat-web test
npm --prefix apps/chat-web run check:agent-types
```

独立合成页面：先以 `mktemp -d /private/tmp/agent-6a1-demo.XXXXXX` 创建专用目录，再将
实际绝对路径填入 `--database`；不要使用现有业务 SQLite。分别在两个终端运行：

```bash
PYTHONPATH=apps/assistant-api/src .venv/bin/python -m apps.assistant-api.tests.browser_session_fixture --database /absolute/demo-dir/agent.db
```

```bash
npm --prefix apps/chat-web run dev -- --config vite.browser-session.config.ts
```

页面 `http://127.0.0.1:13006/`，API 仅监听 `127.0.0.1:18086`。工厂忽略环境及 dotenv，
只装配 RuleBased Understanding 与显式 MockTransport；fixture 中的 Key 全是测试占位。
该服务是演示夹具，不是生产启动入口。停止两个进程后，数据库仍留在指定演示目录。

## 5. 缺口与下一顺序

| 切片 | 状态 | 验收目标 |
| --- | --- | --- |
| 6A-1 访客身份 | Local complete | 本文范围；非登录/RBAC，不承诺个体撤销或跨实例配额 |
| 6A-2 SQLite 持久化与恢复 | Local complete（2026-09-10） | 受控目录 / 整库租约、停服备份、新目录恢复与断网命名卷演练；44 项新增回归覆盖 owner / checkpoint / 幂等 / 收据、继续 / 重放 / TTL / 删除；见 [6A-2](agent-kernel-phase6a2-sqlite-recovery.md) |
| 6A-3 受控部署与 CI | Local verified；remote CI pending | 独立入口 / Compose / 冻结镜像、HTTPS / Host / 公开路由及新卷恢复 / SSE / V1 回退已本地验证；CI 工作流已实现，远程待授权提交推送。见 [6A-3](agent-kernel-phase6a3-controlled-deployment.md) |
| 3B-T T4 / 3B-P P4 | Deferred | 接口可达后确认签名/字典/时间/金额/计价身份，再单独授权真实联调 |
| Phase 5 holdout | Deferred | 人工审核、冻结代表性新数据及另行模型预算；不复用 development 冒充独立测试 |
| Phase 6 完整部署 | Not complete | 生产 checkpointer、多副本锁/幂等/配额、回滚与运维，不能用单 SQLite Demo 替代 |

额外限制：本地历史仍保存在 localStorage；未进行登录、CSP/XSS 专项审计、bot 防护、
真实 TLS 和代理容器验收。一个主机名应只承载本信任域的 Cookie；私有运维路由不得
因 Nginx 的 `/api/` 转发而对公网开放。上游业务授权与匿名访客身份必须分别设计。
