# ADR 0017：代理服务身份与匿名访客会话归属分离

- 状态：Accepted / 6A-1 local verified
- 日期：2026-09-09
- 前序：[0004](0004-memory-and-checkpoint-boundaries.md)、[0007](0007-v1-v2-api-compatibility.md)

## 背景

原 V2 使用已验证 API Key 的哈希作为 owner。对独立服务调用方成立，但 Nginx 给所有
浏览器注入同一个 Key，使这些访客共享 owner。UUID 难猜不能代替访问控制；LangGraph
thread_id、前端 localStorage 中的 conversation_id 都不是可信身份。

## 决策

1. 默认关闭的 browser-session 配置为一个专用代理 Key 声明角色。该 Key 必须属于
   API Key 白名单；浏览器只通过同源代理访问，密钥不进入 VITE 配置或 JS。其他可信
   服务 Key 保持旧行为，不能调用浏览器 bootstrap。已有 owner 不自动迁移到新访客。
2. 分离 `client_id` 与 `agent_owner_id`：前者仍是服务 Key 的哈希，用于共享限流；
   后者来自 origin-bound、HMAC-SHA256 签名的随机 256-bit 访客 ID。API 将其传给
   既有 owner 门禁；Graph、Tool、收据、checkpointer 无需理解 Cookie 或代理协议。
3. Cookie 不承载业务或用户资料，使用 HttpOnly、SameSite=Strict、Path=/、无 Domain；
   HTTPS 默认使用 Secure / `__Host-spb-agent`。仅显式 loopback HTTP 开发可使用
   非 Secure 的 `spb-agent-local`。采用随机会话标识和 Cookie 约束，但不据此宣称达到
   完整企业认证标准，参考 [OWASP Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)。
4. 仅代理角色可 `POST /v2/agent/browser-session`。不安全方法要求精确配置的 Origin；
   存在 Fetch Metadata 时只接受 same-origin，不接受 null Origin 或 same-site 放宽。
   业务接口另要求 Cookie 对应的 `X-Agent-Session` 非敏感引用。不能把 SameSite 单独
   当作 CSRF 防护，参考 [OWASP CSRF Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)。
5. 先 bootstrap，再核验本地历史的 session_ref，再允许业务请求。身份变化/到期时清空
   内存展示并中止消费流，不自动重放待发送消息到新 owner。重新开始删除当前会话；
   重建访客身份则丢弃本机旧绑定，旧服务端数据仍按 TTL 清理，两者语义不同。
6. 固定绝对有效期，不做滑动续期。普通核验不发 Set-Cookie，避免旧标签页延迟响应
   覆盖新重建 Cookie；首次建立或显式 reset 才签发。允许当前及一个前代签名 Key，
   老 Cookie 在宽限期内原样校验，不重新签发或延长寿命。

## 权衡与限制

- 这是匿名访客隔离，不是登录、RBAC、租户/计价资格、强制注销、反机器人或 XSS 防御。
  同浏览器同 origin 的标签页共享访客；共享设备仍需清理聊天记录。
- 无状态 Cookie 无逐访客撤销表；reset 不撤销已复制的旧 Token，提前撤销需移除对应
  签名 Key（影响整批会话）或后续采用服务端 session store。并发首次建立/并发显式
  reset 仍可能竞争，引用不匹配时 fail-closed 并要求重新核验，不能重发业务请求。
- 单进程共享网关限流避免重建身份绕过总额，但不提供访客公平性，也不能代表分布式配额。
- Cookie 不按端口隔离；一个 hostname 上不应部署互不信任的多个应用。TLS、Host 白名单、
  公共路由范围、CSP、持久化与实际 Compose/CI 验收仍属后续部署工作。

实现、54 项后端 / 15 项 Web 新增回归及浏览器竞态验证见
[6A-1 交付说明](../agent-kernel-phase6a1-browser-identity.md)。
