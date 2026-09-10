# Phase 6A-3：独立受控部署、离线 CI 与回退演练

> 状态：Local / synthetic verified，2026-09-10。GitHub Actions 工作流已落地，但尚未提交、
> 推送或在远程执行。不是生产发布或完整阶段 6 验收；真实 `.env` 与已有数据库未读取、未迁移。
> 后续[工具链收口](agent-kernel-phase6a3-ci-closeout.md)已升级 Vitest 4.1.11，完整 npm audit
> 为 0，Python 1044 / Web 70 通过；下文初次演练的 1041 项与镜像证据保留为历史基线。

## 1. 本阶段完成什么

承接 [6A-1 访客身份](agent-kernel-phase6a1-browser-identity.md) 与
[6A-2 整库恢复](agent-kernel-phase6a2-sqlite-recovery.md)，将已有能力装配成独立的单实例
Web / API / 持久卷部署，并用真实 Nginx、HTTPS 和容器重建验证边界。

| 交付 | 当前证据 | 不代表 |
| --- | --- | --- |
| `deployed_app` 与独立 Compose | 无 dotenv 的显式配置、单 worker、严格身份 / managed 配置、空能力 readiness 503 | 默认 V1 栈已切换、真实五能力已可用 |
| 独立 API / Agent Web / V1 Web 镜像 | 基础镜像 digest 固定，`uv.lock` / npm lock 冻结构建，前端模式与代理模式绑定 | 应用 tag 不可变、全平台验证、OS 漏洞扫描通过 |
| HTTPS 公共边界 | Host / Origin 拒绝、精确路由与方法、服务端注入 Key、Cookie 与双访客隔离 | 登录 / RBAC、互联网暴露批准、完整企业网关 |
| 停服备份 → 新卷恢复 → V1 → V2 | 完成响应免调用回放、暂停补槽经 SSE 继续、V1 JSON/SSE 与切回 V2 | 跨版本数据库降级、任意 crash repair、多副本或业务 exactly-once |
| `.github/workflows/agent-ci.yml` | 构建 / 测试 / 类型 / 13 场景 Eval / Docker 演练自动工作流已实现，本地对应检查通过 | GitHub 远程绿灯、分支保护或自动部署已开启 |

实施范围未改变 Domain、LangGraph Node、公开 API 或 OpenAPI 合同。运维与信任边界留在
入口 / Nginx / Compose / CI，继续复用既有 Graph、Tool、身份中间件和存储 CLI。

## 2. 装配边界与启动合同

```text
回环 HTTPS 客户端
  → Web / Nginx（唯一发布端口，专用 ingress 网络）
      → 私有内部网络 → Assistant API（单实例、无 host port）
                           → managed runtime-data / 恢复后 restored-data

state-init（无网络，UID 10001）→ 初始化或验证目录 → API ready → Web 启动
storage（手动 operations profile，无网络）→ 停服快照 / 校验 / 新目录恢复
```

- API 和 Web 均以 UID/GID 10001 运行，根文件系统只读、丢弃 capabilities、禁止提权，
  设置内存 / CPU / PID 上限。Nginx 的配置、pid 和所有临时目录写入私有容器 tmpfs。
- API 默认只连接 `internal` 网络；Web 同时连接入口网络以承接发布端口。只发布
  `127.0.0.1:13443`（可改回环端口），不宣称整栈 `network_mode: none`。
- `state-init` 只创建全新受控目录，`--if-absent` 只验证受控旧目录；失败阻止 API 启动。
  API 的 V2 readiness 通过后 Web 才启动。Compose 启动条件依据
  [官方启动顺序说明](https://docs.docker.com/compose/how-tos/startup-order/)。
- `DeploymentSettings` 只读取显式初始化参数和环境，不加载仓库或应用 `.env`。要求
  鉴权、浏览器身份、HTTPS Cookie、managed 存储一致开启；代理 Key 与 API 角色映射一致，
  签名 Key 独立。V1 回退要求 V2 / browser / managed / tracking 关闭，不访问 Agent 数据库。
- 基础 Compose 没有 `env_file` 或模型 / 物流自动开关；部署操作者必须传入明确的
  `--env-file`。示例只有占位符，不能直接上线；不要把渲染后的 Compose / Nginx 配置打印到日志。
- 受控入口**不导入或降级到 Fake**。既有 Policy / Device Tool 只有显式配置批准的 RAG /
  MySQL 依赖才可用；可选 dependencies overlay 连接已有外部网络，不启动这些依赖。
  全部依赖为空时 readiness **503**、能力均 unavailable，是预期拒绝，不应改成 200 来启动 Web。
- 合成 overlay 明确选择 `deployment_demo`，只允许本机 HTTPS Origin，忽略所有环境中的
  `ASSISTANT_*` 与 dotenv，仅注入固定 Fake Tracking / V1 Tool、规则理解及本地聚合指标。
  不装配模型、真实物流 transport、SQL/RAG 或遥测 exporter；它不是发布入口。

TLS 合成夹具只生成一天有效的本机证书。一次性、无网络的 `tls-init` 以 root + 两项有限
capabilities 将新证书复制至独立合成卷，设置私钥 0600 / UID 10001 后退出；不是让常驻
API / Nginx 以 root 运行。受控部署不带该服务，证书及 UID 可读权限由操作者预先准备。

## 3. HTTPS、代理身份与公开路由

| 公共路由（Agent 模式） | 方法 | 用途 |
| --- | --- | --- |
| `/`、`/assets/…` | 静态资源 | 固定构建产物，不向浏览器注入服务 Key |
| `/api/v2/agent/browser-session` | POST | 签名 Cookie 建立 / 核验 |
| `/api/v2/agent/capabilities` | GET | 公开能力描述 |
| `/api/v2/agent/messages` | POST | JSON / SSE，共用既有幂等合同 |
| `/api/v2/agent/conversations/{canonical-uuid}` | DELETE | 由 owner 门禁保护的删除 |

其余 API、metrics、readiness、docs、OpenAPI、dotenv 路径均 404；不向未知 API 返回 SPA。
允许的业务路径使用错误方法返回 405。V1 模式只开放 `/api/v1/chat` 的 POST，不保留 V2
入口。内部 liveness / metrics 仅供容器探测和合成演练读取，没有公共代理。

Host 必须匹配配置 authority，不匹配返回 421；只启用 TLS 1.2 / 1.3。API 仍执行精确
Origin 与 Cookie / session_ref 校验，拒绝跨访客恢复。代理替换 Authorization 并清除
客户端传入的 API Key、owner、user 与 Forwarded 家族身份头；Uvicorn 不信任 proxy headers。
Key 先做字符 / 长度校验，再经限定变量的 envsubst 写入 0600 tmpfs 配置，不成为 build arg。

SSE 禁用代理缓存与缓冲；关闭 `proxy_next_upstream`，避免代理层自行重发非幂等的工具
推进请求。Agent read timeout 40 秒，覆盖本栈固定默认的 30 秒请求预算；V1 为 125 秒。
若以后调整后端预算，必须一起调整并测试代理超时，不能任意注入更长超时后沿用本配置。
请求体最大 1 MiB，另有 header / body / connect 超时。

安全响应头包含 CSP、nosniff、no-referrer、frame deny、权限策略和 no-store。CSP 的
`style-src 'unsafe-inline'` 为现有 Vue 样式保留；未配置公网 HSTS / preload，避免把本机
演练策略等同公共域名策略。路由行为依据
[Nginx HTTP 核心模块](https://nginx.org/en/docs/http/ngx_http_core_module.html)。

## 4. 恢复与回退不是同一操作

1. 服务运行中备份返回 `store_busy`，禁止绕过租约。
2. 停止 Web 与 API，使用 storage profile 备份整库、verify，并恢复到新的 restored 卷。
3. 保持 Origin、Cookie 签名 Key 和代理 Key，显式加入 restored overlay 切换数据库路径。
4. 已完成请求返回原收据，`execute_tool` 节点计数为 0；暂停请求补齐邮件号后通过 SSE
   继续，计数只增加 1；同一幂等请求再次重放不增加计数。
5. 停服后加入 legacy overlay，同时切换 API 挂载和 Web 构建 / 路由。V1 JSON/SSE 正常；
   Agent 数据仍保留，但不被 V1 打开。此处 liveness 不是依赖 readiness，需要另验 V1 能力。
6. 撤销 legacy overlay、保留 restored overlay 切回 V2；原 Cookie 与请求仍可免执行回放。

这验证同一应用版本内 API/UI 模式回退，不是旧二进制加载新 schema。真实升级须先记录
代码 / 镜像 / profile / 依赖版本，单独验证迁移与反向兼容；不能直接降级 SQLite 文件。
完整操作见 [部署 runbook](../deploy/agent/README.md)，沿用 6A-2 的绝对 TTL、不覆盖、
未完成 claim 拒绝、备份信任和删除历史限制。

## 5. CI 与安全依赖门禁

工作流使用 GitHub 托管 Ubuntu 24.04，触发 PR / main、develop push / 手动运行：

- `contracts`：冻结 Python / npm 安装 → npm 全依赖 moderate 安全门禁 → 全量 Python / 架构测试 →
  独立公开 V2 Mock Eval → Web 单测 → OpenAPI 生成一致性 → vue-tsc → Agent / V1 隔离构建。
- `synthetic-deployment`：依赖上一步成功，再构建三个镜像并运行有限 Docker HTTPS 恢复 /
  回退演练。缺依赖或断网安装失败会显式失败，不跳过或用 `continue-on-error` 通过。
- 第三方 Actions 固定完整 commit SHA，权限仅 `contents: read`，checkout 不保留凭据；
  不使用 `pull_request_target`、真实 secrets、自托管主机、镜像推送或远程部署。
  依据 [GitHub Actions 安全指南](https://docs.github.com/en/actions/reference/security/secure-use)。
- 依赖安装 / 镜像拉取需要公共软件仓库；“offline”指业务评测无真实模型 / 供应商调用，
  不等于整条构建流水线没有网络。合成输入不是 holdout，也不是生产质量证明。

初次切片将锁文件内 nanoid 3.3.17 升至 3.3.18，关闭 1 项 high，当时 runtime audit 为 0、
Vitest / mocker 仍有 2 项 moderate。随后[工具链收口](agent-kernel-phase6a3-ci-closeout.md)
将 Vitest 家族升至 4.1.11，完整 npm audit 为 0；增加默认不读 dotenv 的测试配置，CI
显式包含 dev 并拦截 moderate 及以上。两个依赖条目对应同一安全公告，不是两个独立漏洞。依据
[NanoID 公告](https://github.com/advisories/GHSA-2v37-7h3g-55p8)与
[Vitest 公告](https://github.com/advisories/GHSA-82fw-gwwq-j7x9)。Python / OS 镜像扫描尚未覆盖。

## 6. 本地证据与复跑

新增 **21 项 Python 回归**：不一致配置拒绝、无隐式 Fake、环境误开模型仍零 HTTP、
重建重放 / V1 不碰原库、合成 Origin 限制、部署 / Nginx / CI 合同和远程 Docker 拒绝。
演练会固定已校验的本地 Docker socket，忽略环境中的其他 Docker context，防止检查
本机后实际操作另一 context。

本阶段全量 Python **1041 passed**、Web **70 passed**；生成类型、vue-tsc 和两种 Web
隔离构建通过。CI 专用 Eval 为 **13 场景 / 28 Turn / 8 次 Mock 供应商调用**，质量门禁通过，
模型 / 真实业务调用为 0。

在仓库根目录运行（已安装锁定依赖、本机 Docker / Compose 与 OpenSSL）：

```bash
.venv/bin/python deploy/agent/smoke.py --build
```

脚本使用随机唯一项目、新私有临时目录与新命名卷，不读取实际配置。客户端仅信任本次
证书，不使用 `verify=False`，不安装系统 CA。输出四组 `PASS`：入口边界；Cookie / 双访客 /
无客户端密钥；新卷恢复 / SSE 继续及回放；V1 回退与 V2 返回。report.json 记录镜像 ID /
平台、实际保留卷名与检查结果，不包含 Cookie 或业务明文。指标是进程内 execute_tool
计数；结合独立 Fake 调用单测验证回放，不将其表述为真实供应商 exactly-once。

2026-09-10 本机完整演练已通过。API 明确以 linux/amd64 在 ARM 主机运行，Web 为本机
ARM 构建，基础镜像按 digest 固定且依赖独立安装，未沿用 6A-2 的旧 Demo 镜像。
冻结 / 非 editable 构建参考 [uv Docker 集成](https://docs.astral.sh/uv/guides/integration/docker/)。
这不是多架构资格验收或性能测试；应用 tag 仍可变，发布前需生成不可变版本 / digest。

最终复跑项目 `spb-agent-6a3-98fd8990324d` 四组检查全部通过，`containers_removed=true`。
其四个卷后缀为 `runtime-data`、`backup-data`、`restored-data`、`synthetic-tls`，实际名称
以项目名加 `_` 前缀。报告目录为本机临时目录 `spb-agent-6a3-aysv95bs`，仅用于本次复核。
镜像 ID（不是仓库发布 digest）为：

| 镜像 | 本次 ID | 平台 |
| --- | --- | --- |
| `intern26-spb-agent-api:6a3` | `6528cadd28ad27bff62cff2c273c3bd1ae5df1efe80473892eac6420cb8732be` | linux/amd64 |
| `intern26-spb-agent-web:6a3` | `00d3dc0fa0439cd57f85b901924f96fe2433381e6d51d34fb6a91aa42c141c1d` | linux/arm64 |
| `intern26-spb-agent-web-legacy:6a3` | `257a0060267d71f664ae687b8e4d09628e129e2cdb475f89669d11d20145e283` | linux/arm64 |

开发联调先后暴露了证书安装权限、BusyBox 正则重复次数上限与仅 internal 网络无法承接
本机发布端口的问题：分别修正先设置权限再转移 UID、使用 shell 长度校验、将入口与私有
网络分开；同时补齐只读 Nginx 的所有临时目录。修复后重跑完整恢复 / 回退，不只检查容器
healthy。收尾删除本次短生命周期容器 / 网络，保留合成卷与私有证书供复核；不使用 `down -v`。

## 7. 缺口与下一阶段顺序

| ID | 状态 / 缺口 | 下一动作与验收 |
| --- | --- | --- |
| D01 | CI remote pending | 用户授权提交 / 推送后，观察远程两项 job；补不可变版本、镜像来源与报告留存，不能用本地结果宣称远程绿灯 |
| D02 | npm closed / broader scanning pending | Vitest / mocker 已升至 4.1.11，完整 npm audit 0；默认测试隔离、moderate 门禁和 Node 22 / 本机回归已完成，见[工具链收口](agent-kernel-phase6a3-ci-closeout.md)。Python / 容器 OS 扫描与补丁策略仍待补 |
| D03 | controlled dependencies pending | 明确目标主机、现有 RAG / SQL 接口和批准网络，逐一验 readiness；禁止启用 Fake 来伪造真实能力 |
| D04 | public release pending | 公共证书 / TLS 挂载与续期、Host / Origin、可信上级代理、HSTS 策略、秘密托管和日志审查需目标环境验证；当前只发布回环 |
| D05 | storage / identity partial | 未覆盖旧库接管、跨版本迁移、加密异地 / 保留、备份外删除账本、个体撤销 / 登录、业务授权、多副本锁 / 配额 / 熔断；沿用完整阶段 6 DoD |
| D06 | business / quality pending | T4 / P4 合同确认与受控真实调用需资料及单独授权；时限文档未到；代表性 holdout 需人工审核、冻结与新增预算，不消耗历史授权 |

测试依赖安全与默认离线门禁已收口。下一步在取得 **Git 提交 / 推送授权后验证远程 CI**，
再安排已批准目标环境单实例验收；不自动扩展业务网络。接口和数据条件齐备后分别执行 T4 /
P4、holdout，最后验收完整阶段 6。核心取舍见
[ADR 0019](adr/0019-controlled-deployment-and-offline-ci.md)与[复盘故事 U](project-retrospective.md)。
