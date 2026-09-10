# Phase 6A-4：既有内网服务的单实例更新

> 状态：In progress，2026-09-10。6A-3 的远程 CI 已验收；本阶段按用户明确要求保留
> 原内网 HTTP 入口，复用既有 RAG 与设备价格库。此文不记录实际主机、账号、密钥或私有目录。

## 1. 本次发布的 Definition of Done

- 单 worker / 单副本、LangGraph + managed SQLite、签名访客身份、补槽 / 恢复、JSON/SSE
  及幂等重放正常；默认 HTTPS 配置仍严格校验，不自动降级。
- policy / device_price 使用原 RAG / MySQL，实际核验连接、记录数及业务请求。
  数据为空应返回无匹配，不等同工具未装配，也不能用合成记录冒充业务数据。
- tracking / postage / delivery_time 不装配真实 Gateway，不加载 Fake；能力列表 unavailable，
  输入命中后直接显示“该查询服务暂不可用，请稍后再试。”，不额外补槽或讲开发状态。
- 新代码全量本地测试、远程 CI 通过，目标架构镜像通过服务器侧验收后才切换入口。
- 保留旧容器、不可变镜像标识、旧运行配置及 RAG 服务；切换失败可恢复原入口，不覆盖业务库。

三项物流真实联调、独立 holdout、多副本、企业登录、异地加密备份不属于这次发布的前置
条件，继续保留在各自缺口台账。这是明确的单实例发布范围，不冒充完整生产 SLA 验收。

## 2. HTTP 是显式且有限的部署例外

用户要求保留 HTTP，不能签发 `__Host-` Secure Cookie 后假装浏览器可用，也不能偷偷
关闭默认 HTTPS 校验。使用独立的 opt-in：

```text
ASSISTANT_DEPLOYMENT_TRANSPORT_MODE=private-http
ASSISTANT_AGENT_BROWSER_PRIVATE_HTTP_ENABLED=true
ASSISTANT_AGENT_BROWSER_COOKIE_SECURE=false
ASSISTANT_AGENT_BROWSER_PUBLIC_ORIGIN=http://<private-ip>:<port>
AGENT_TRANSPORT_MODE=private-http
AGENT_PUBLIC_ORIGIN=http://<same-private-ip>:<same-port>
```

- 只接受规范的 RFC1918 IPv4；127/8 仅供回环验证。拒绝公网 IP、DNS 名称、通配绑定
  origin、链路本地、凭据 / 路径 / 查询串、非规范 IPv4 与端口。
- 使用独立的 `spb-agent-intranet`、HttpOnly、SameSite=strict、Path=/，不设 Domain；
  保持绝对 TTL、随机 owner、HMAC、同源核验、session_ref、双访客隔离与代理独立 Key。
- 默认模式仍为 HTTPS + `__Host-spb-agent` + Secure；不完整 opt-in 在打开数据库前失败。
- Nginx 的 Host、方法 / 路由白名单、CSP、no-store 和身份头清理在两模式中共用。
  后端不发布 host port；只将 Web 发布至用户批准的内网地址，不绑定所有网卡。
- HTTP **没有传输加密**，不能防止同网段窃听、篡改、Cookie 注入或会话劫持；HMAC 和
  SameSite 不能替代 TLS。不得将该模式作为公网、安全登录或敏感数据传输的合格方案。
  此限制记录在运维文档，不给用户界面添加“开发未完成”提示。

## 3. 既有主机兼容与数据保留

先只读确认：目标平台、Docker 版本、容器 ID / 镜像 ID、发布端口、网络、挂载、依赖
端点与只读账号是否配置。使用容器原生凭据检查，不输出 `.env`、DSN 密码或模型 Key。

旧主机未必有 Compose / Python 3，不直接升级共享 Docker daemon。交付 Linux amd64
镜像，通过 SSH / Docker 原生命令运行相同 entrypoint；在私有新目录准备配置及 managed
存储，先旁路验证，再切换 Web 端口。不能用关闭 seccomp、root 常驻或删除原容器绕过兼容问题。

RAG 及原业务数据库不重建、不清库、不重新索引；只新增 Agent 状态存储。readiness、
表 / 集合实际可读、具有有效记录、端到端查询成功分别验收，不能由 `SELECT 1` 推断存在价格。
发现空库仅记录并询问原数据状态，不自动换数据库、抓取或导入。

现有政策查询的预算需与新 Agent / 代理预算一致：根据原服务耗时显式设置后端请求上限
（最多 120 秒），配套 `AGENT_READ_TIMEOUT_SECONDS`（允许 40–135 秒，通常 130）。
仍不自动重试代理 POST，也不延长模型、物流的单次请求预算。

### 3.1 旧 Docker 的线程兼容入口

目标主机的旧默认 seccomp 对 `clone3` 返回 EPERM，glibc 因而不回退到 `clone`，导致
aiosqlite / LangGraph 创建线程失败。不是存储损坏，也不是线程数超限。没有继承旧容器的
`seccomp=unconfined`，没有升级共享宿主机，也没有放松默认运行入口。

仅在已核验的 Linux amd64 旧主机使用 `python -m spb_assistant_api.legacy_deployed_app`：
在导入应用、创建线程之前，要求已有 seccomp filter 与 no-new-privileges，叠加六条 BPF
指令，仅对 amd64 的 `clone3` 返回 ENOSYS，使 glibc 回退到父过滤器已允许的线程调用。
其他调用继续受父过滤器约束；不满足前置条件或安装失败时拒绝启动，不自动使用 unconfined。
叠加过滤器不能放行父过滤器拒绝的调用，errno 行为依据
[Linux 内核 seccomp 文档](https://docs.kernel.org/userspace-api/seccomp_filter.html)；
clone3 兼容问题参见 [Moby 修复](https://github.com/moby/moby/pull/42681)。

目标主机在非 root、只读根文件系统、drop ALL capabilities、无业务网络的探针中，已实际
通过线程创建与持久化 LangGraph / SQLite 打开。Mac 跨架构模拟器不能通过 seccomp 前置
检查，这不算验收通过，也不绕过校验：完整兼容演练放在原生 Linux amd64 CI，
`smoke.py --transport private-http --legacy-threads`；普通 HTTPS / HTTP 演练仍可在本机运行。
Alpine 替代路线已因锁定的 sqlite-vec 缺少 musllinux wheel 而放弃；未改依赖锁或删减功能。

### 3.2 Web 的发行版兼容

API 就绪后，旁路 Nginx 1.31.3 / Alpine 3.24 在写 PID 时仍返回 `pwrite: EPERM`。
同类问题已在 [Nginx 官方镜像仓库](https://github.com/nginx/docker-nginx/issues/1059) 报告。
没有通过改 PID 权限、降级 Nginx 或关闭 seccomp 掩盖错误；改用**同一 Nginx 版本**的
官方 Debian 镜像，固定注册表 digest，并将健康检查改为镜像自带的 `curl`。
目标原生主机在原隔离条件下已健康启动，Agent / V1 两种 Web 镜像重新构建，
默认 HTTPS 与显式 HTTP 的四段恢复 / 回退演练重新通过。

## 4. 验证顺序与证据

1. 33 项新增回归：私有 origin 正反例、完整 opt-in、默认安全边界、两核心能力 / 三项
   unavailable、双 owner、同源拒绝、核心响应与免调用重放；无真实业务网络。
2. 另有 10 项旧主机兼容回归覆盖 ABI / syscall 边界、安装顺序及失败关闭。
   再补 1 项 Debian 健康检查合同，全量 Python **1089 passed**、Web **70 passed**、
   生成 API 类型一致（本地验证）。
3. 同一 Docker smoke 已分别通过默认 HTTPS 与显式 `--transport private-http`，各四组 PASS：
   路由 / 身份、停服备份、新卷恢复、SSE 与 V1 回退；只连接回环、合成数据，保留演练卷。
   最新 Debian 版本报告为临时目录 `spb-agent-6a3-5ao8joyo/report.json`（HTTPS）与
   `spb-agent-6a3-lo4uiuwj/report.json`（HTTP），两者 `status=passed`、`containers_removed=true`。
4. `6a7a79a` 的[远程 CI](https://github.com/wwbotww/Intern26-spb/actions/runs/34444339412)已全绿，
   包括原生 Linux 完整线程兼容演练；Debian Web 后续提交与正式入口切换单独验收。

```bash
.venv/bin/pytest
npm --prefix apps/chat-web test
.venv/bin/python deploy/agent/smoke.py --build
.venv/bin/python deploy/agent/smoke.py --transport private-http
# 原生 Linux amd64；不可通过关闭 seccomp 在模拟器上强行运行
.venv/bin/python deploy/agent/smoke.py --transport private-http --legacy-threads
```

## 5. 后续独立事项

- 空价格库的数据恢复 / 导入需原数据来源和单独授权。
- 更换受支持的主机 OS / Docker、TLS 网关、真实备份保留策略另行安排，不在共享主机上
  自动执行平台升级；本次验收只承诺已测单实例兼容路径。
- T4 / P4 / 时限文档、代表性 holdout、登录 / RBAC、多副本协调沿用原计划。
