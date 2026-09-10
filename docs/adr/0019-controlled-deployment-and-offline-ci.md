# ADR 0019：分离受控部署和合成验收，用公开 HTTPS 路径验证恢复与回退

- 状态：Accepted / 6A-3 local verified；远程 CI execution pending
- 日期：2026-09-10
- 前序：[0017](0017-browser-visitor-identity.md)、[0018](0018-quiescent-agent-storage-snapshots.md)

## 背景

图执行、匿名 owner 和 SQLite 恢复均有本地测试，但实际代理可能暴露内部路由、丢失
Cookie / SSE 语义，或在 Web / API 配置错配时绕开身份。只证明 Graph 能 resume，不能
证明部署后原访客仍能安全继续；直接复用含本地配置的 Demo 也不能成为 CI。

## 决策

1. 保留原默认 V1 部署，新增独立 `deploy/agent/` 与不读取 dotenv 的 `deployed_app`。
   强制服务鉴权、HTTPS 访客身份、managed 存储和单 worker 的一致配置；空能力 readiness
   503，不注入 Fake 兜底。已有业务依赖必须显式配置并接入批准的网络。
2. 单独的合成 overlay / composition root 只接受回环 HTTPS，注入固定工具与规则理解，
   不咨询真实配置、模型、供应商或 exporter。CI 不接触秘密，不启用真实业务能力。
3. 前端构建模式与 Nginx 路由模式绑定；服务 Key 只在代理启动时进入私有 tmpfs，不进入
   构建参数或静态资源。Host、公开路由与方法限制在边缘，Origin / Cookie / owner 仍由
   API 校验；清除伪造身份头，不让代理重试推进请求。
4. 初始化、常驻运行与运维 CLI 分开。API / Web 非 root、只读根目录、资源限制；API
   无发布端口、只连内部网络；Web 独立承接回环 HTTPS。TLS 合成 provisioner 是短生命周期
   例外，持有限定权限且不读取真实证书；正式配置不包含它。
5. 锁定基础镜像 digest，用 uv / npm lock 构建独立镜像。CI 固定 Actions SHA 与最小
   权限，顺序执行合同 / Eval / 类型 / 两种 UI 构建和真实 Docker 合成演练。参考
   [GitHub Actions 安全指南](https://docs.github.com/en/actions/reference/security/secure-use)。
6. 恢复先停服，再整库备份 / 校验，只写新卷；保留密钥 / Origin 和收据语义。V1 回退同时
   切换 API 与 Web，关闭而非降级 / 删除 Agent DB；切回 V2 后继续验证免执行回放。
7. 演练仅操作已校验且固定的本地 Docker socket，随机项目名，无隐式 env 文件；客户端
   信任本次自签证书而不关闭校验。报告记录实际镜像 ID / 平台 / 保留卷，收尾不删卷。

## 取舍与验收边界

- 对已有真实配置 fail closed，会让未配置依赖的受控栈无法 ready；这是能力真实性门禁，
  不是用演示数据改善可用性的场景。合成栈有独立入口、数据来源和报告，不是生产 fallback。
- 公共 API allowlist 在新增路由时需要同步维护；增加合同回归和代理 HTTP 黑盒检查，避免
  只修改后端就意外发布内部端点。节点不负责 TLS、Cookie 或容器生命周期。
- 单实例停服换取可验证的恢复点，但不能修复任意 crash 窗口。UI/API 回退不等于数据库
  schema downgrade；旧二进制兼容、外部删除账本及多副本仍待独立验收。
- 工作流文件和本地绿灯只是可执行门禁的工程交付，远程 CI、分支保护、公网证书、镜像
  发布与供应商联调分别验收。本次无自动提交 / 推送 / 部署，不消耗模型预算。
- 初次验收时 npm high / runtime 为 0、Vitest 相关 2 项 moderate 待升级；随后
  [工具链收口](../agent-kernel-phase6a3-ci-closeout.md)已升级 4.1.11、默认测试隔离与 CI
  全依赖 moderate 门禁，完整 npm audit 为 0。Python / OS 扫描、正式秘密管理 / 轮换、
  HSTS、登录 / 业务授权均不能据此宣称完成。

实现、21 项新增回归、合成 HTTPS / 新卷恢复 / V1 回退证据及缺口见
[6A-3](../agent-kernel-phase6a3-controlled-deployment.md)，操作见
[部署 runbook](../../deploy/agent/README.md)。
