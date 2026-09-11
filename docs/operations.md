# 跨服务运维与发布交接

本页维护部署组合、Web/API 入口安全、共享监控与发布交接。
Assistant 的配置/状态库/排障见[服务运维](../apps/assistant-api/docs/operations.md)；
真实能力见[当前状态](current-status.md)，发布依据见[交付摘要](history/agent-delivery-summary.md)。

## 部署组合与配置配套

| 模式 | 配置 / 用途 | 操作入口 |
| --- | --- | --- |
| 本地五能力 / 资费 Demo | 合成工具或 Mock；只监听回环，不能处理真实业务 | [开发指南](development.md) |
| 受控 Agent 栈 | `deployed_app`、匹配 Agent Web、私有配置、managed SQLite | [Agent 部署手册](../deploy/agent/README.md) |
| 原有 RAG / V1 / Web 栈 | 旧版显式单轮接口，无 Agent 状态持久化 | [V1 部署基线](deployment.md) |
| 特定服务器发布 | 私有 manifest 中的实际入口、镜像、环境、网络/卷 | 先核对 `active-release.json`，不能用通用默认值推断 |
| 合成观测 / 存储演练 | 独立项目、隔离数据，不改生产服务 | 本页监控说明、[存储演练](../deploy/storage/README.md) |

`deploy/agent` 基础 Compose 用仓库外私有 env-file 和独立项目，
模型/tracking/exporter 固定关闭，不等于映射了全部 Assistant Settings。
它与特定服务器发布并非完全相同的配置方案；不能因存在 Key 自动打开模型，
或把 synthetic overlay 加到真实项目。未知能力保持 unavailable，全部不可用时 ready=503。

| 跨服务配套项 | 约束与维护入口 |
| --- | --- |
| Web/API 模式 | Agent UI、访客协议与后端装配一致；[Web 开关](../apps/chat-web/README.md)在构建时生效 |
| 服务 Key 与访客身份 | Nginx 持有代理 Key，后端白名单/角色匹配；独立 Cookie 签名 Key 不复用服务 Key |
| RAG / MySQL 数据 | 指定已有目标与只读账号；[数据源边界](data-sources.md)记录所有权，健康不等于答案覆盖 |
| 请求预算 | 代理与 Agent 总预算覆盖政策链路；不要为入口超时随意放大模型/物流单次预算 |
| SQLite / 镜像 | 单 worker、固定 UID/权限、匹配存储 profile；恢复不能顺带迁移 schema 或升级依赖 |

字段入口为 [Assistant 示例](../apps/assistant-api/.env.example)与
[部署示例](../deploy/agent/.env.example)。Key、DSN、证书和签名 Key 不入 Git，
配置文件最小权限；不要输出完整 `docker inspect`、`compose config` 或 `nginx -T`。

## 入口安全与兼容

默认 HTTPS、精确 Host/Origin、Secure/HttpOnly/SameSite=strict Cookie。
只让 Web 暴露必要路由，后端不发布 host port；ready/metrics/调试限制运维网络。
代理服务准入不等于访客 owner；身份协议、轮换与恢复约束见
[Assistant 运维](../apps/assistant-api/docs/operations.md)。

### 显式内网 HTTP 例外

仅在用户明确批准的私有 IPv4 入口成组设置，不能只关闭 Secure：

~~~text
ASSISTANT_DEPLOYMENT_TRANSPORT_MODE=private-http
ASSISTANT_AGENT_BROWSER_PRIVATE_HTTP_ENABLED=true
ASSISTANT_AGENT_BROWSER_COOKIE_SECURE=false
ASSISTANT_AGENT_BROWSER_PUBLIC_ORIGIN=http://<private-ip>:<port>
AGENT_TRANSPORT_MODE=private-http
AGENT_PUBLIC_ORIGIN=http://<same-private-ip>:<same-port>
~~~

仅支持规范 RFC1918 IPv4（回环仅供演练），不接受公网 IP/DNS/通配 Origin/URL 凭据。
改用独立 spb-agent-intranet Cookie，仍有签名/同源/会话隔离；
**HTTP 没有传输加密，不能防窃听、篡改或会话劫持**。该例外不可推广为公网方案。
风险留在运维文档；访客界面只显示正常业务状态。

### 已验证旧主机兼容

旧 Linux amd64 Docker 的 seccomp/clone3 问题使用独立
`python -m spb_assistant_api.legacy_deployed_app`：
要求既有 filter/no-new-privileges，仅叠加 clone3→ENOSYS，让 glibc 回退。
不关闭 seccomp、不提权、不自动升级共享 daemon；正常入口不隐式使用兼容分支。
原生 Linux CI 单独验证，Mac 跨架构模拟器失败不能冒充通过。

Web 使用固定 digest 的同版官方 Debian Nginx 和 curl 健康检查，
规避旧主机上的 Alpine PID 写入兼容问题；镜像来源/构建见 `deploy/agent/`。

## 备份与回退的跨服务边界

先停 Web 接流量与 API 写入，再按[部署手册](../deploy/agent/README.md)获取整库快照，
恢复到全新目录，使用匹配版本隔离验收。不能只复制正在写入的 SQLite，
也不能用“切回 V1”代替数据库降级。恢复后备份源必须切换到实际运行库；
具体表、租约、错误处理和隐私限制只在
[Assistant 状态库运维](../apps/assistant-api/docs/operations.md)维护。

回退时 Web、代理模式和 API 必须配套，原 Agent 库保留不打开/删除/降级。
私有 Origin、签名 Key、服务 Key 和依赖配置要单独匹配，数据库备份不包含秘密。

## 本地合成监控栈

[Assistant 观测边界](../apps/assistant-api/docs/operations.md#5-观测边界)
定义 run / invocation / Node span 与脱敏口径；本节只维护跨组件栈操作。
独立合成监控栈不加载 dotenv、不调用模型/业务数据，仅回环：

~~~bash
docker compose -f deploy/observability/docker-compose.yml up -d --build
# 等 Agent 与 Tempo ready 后再执行
.venv/bin/python deploy/observability/smoke.py
~~~

Agent 18081，Grafana http://127.0.0.1:13001/d/agent-workflow，
Prometheus 19091，Tempo 13200，OTLP HTTP 14318/v1/traces。
匿名 Viewer 可在 Trace 表进入只读详情查看 Node spans，不需提升编辑权限。
指标全量/Trace 采样不能直接混算成功率，合成 P95 不是 SLA。

停止：`docker compose -f deploy/observability/docker-compose.yml down`。
tmpfs 会丢演示会话/指标/Trace，需证据先保存摘要；不要转发公网。

## 发布交接最小清单

私有位置须交给维护者：active-release.json、验收记录、镜像不可变 ID、配置/密钥位置及权限、
实际网络/端口/卷、备份源与恢复点、上一 Web/API、匹配版本的回退脚本、负责人。
公共仓库只记录这些项目与查找流程，不记录主机账号/密码或绝对私有目录。

已有内网发布的 root-only rollback-to-v1.sh 先核对镜像，再切旧 Web；
旧 API/RAG 保留，Agent 存储不删不降级。更新前核对 active manifest，
不能将该脚本用于任意后续版本。实际正式入口回退未执行，演练证据来自合成流程。

发布需分别确认 CI、旁路、数据/依赖、目标浏览器与回退；不能由容器 healthy 推断全部完成。
企业登录、多副本、跨版本迁移、异地加密和真实监控在[路线图](roadmap.md)另行验收。
