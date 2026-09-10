# 6A-3 独立单实例 Agent 部署与合成演练

与 `deploy/docker-compose.yml` 的 V1 栈分开，不迁移原数据库、不读取默认 `.env`。
详细职责 / 证据 / 缺口见 [6A-3](../../docs/agent-kernel-phase6a3-controlled-deployment.md)。
这是回环入口的受控装配基线，不是公网一键发布；未批准真实物流、模型或数据访问。

## 1. 无真实配置的一条命令演练

前提：仓库根目录、锁定 Python 依赖（含 httpx）、本机 Docker / Compose 与 OpenSSL。
构建需要公共软件仓库，不需要业务 API Key；只接受本机 Unix Docker socket。

```bash
.venv/bin/python deploy/agent/smoke.py --build
```

已构建三个当前镜像时可省略 `--build`；`--port 13443` 可指定空闲回环端口。脚本不加载
任何 `.env`，随机项目、新证书和新卷；所有业务数据为合成，真实业务 / 模型调用为 0。
它验证 HTTPS / Host / Origin / 路由、两访客 JSON、SSE、在线备份拒绝、停服整库备份、
新卷恢复、幂等回放、V1 回退及返回 V2。客户端只信任该证书，不改系统信任库。

预期四组 PASS 和 report.json 路径。报告含镜像 ID / 平台、实际卷名、结果，不含 Cookie。
无论成功失败都停止并移除本次容器 / 网络；卷、证书和失败诊断保留，便于复核。
如果失败，先看该临时目录的 `synthetic-diagnostics.log`，不要通过关鉴权、TLS 或租约重试。
每次重试使用脚本新建的项目，不能复用生产项目名。

## 2. 受控栈准备（不是 synthetic overlay）

只有明确目标环境与依赖访问许可后才执行本节；不从用户现有 `.env` 自动推导配置。

1. 将本目录 `.env.example` 的字段填写到**仓库外**私有文件（0600），不要使用示例空值。
   `AGENT_PROXY_API_KEY` 为独立高熵服务密钥，16～256 位 ASCII 字母 / 数字 / `._-`；
   `AGENT_BROWSER_SIGNING_KEY` 为不同的高熵签名密钥（至少 32 字符）。不要发到聊天或 Git。
2. Origin 为精确小写 HTTPS DNS / IPv4 authority（含实际非默认端口，无路径 / 尾斜线）；
   当前入口不支持 IPv6 authority。发布地址仍固定回环，外部域名 / 上级代理需单独设计。
3. `AGENT_TLS_DIRECTORY` 必须已存在，只含批准的 `server.crt` / `server.key`，只读挂载。
   确保证书 SAN 匹配 Origin，容器 UID 10001 能遍历目录并读私钥；文件最小权限，禁止 777。
   macOS bind UID 行为与 Linux 不同，不能将合成 `tls-init` 当作真实证书管理方案。
4. 受控服务无 Fake 兜底。填写已批准的 RAG / MySQL 依赖以及
   `AGENT_DEPENDENCY_NETWORK`（已存在的 Docker network），并使用 dependencies overlay。
   未配置的能力保持 unavailable；**全部为空则 readiness 503、Web 不启动**，不要绕过门禁。
5. 环境变量优先级可能覆盖 `--env-file`，启动前核对同名 `AGENT_*` 的来源。不要输出
   `docker compose config`、`docker inspect` 全量环境或 `nginx -T`，它们可能包含密钥。
   模型、tracking、exporter 在本基础栈中固定关闭；资费真实 transport 尚未装配。

以下变量只代表操作者明确选定的文件与**独立项目**，不是可直接运行的实际配置：

```bash
AGENT_DEPLOY_ENV=/absolute/private-config/agent.env
AGENT_DEPLOY_PROJECT=spb-agent-controlled
agent_compose() {
  docker compose --env-file "$AGENT_DEPLOY_ENV" --project-name "$AGENT_DEPLOY_PROJECT" \
    -f deploy/agent/docker-compose.yml -f deploy/agent/docker-compose.dependencies.yml "$@"
}
agent_compose config --quiet
agent_compose build assistant-api chat-web
agent_compose -f deploy/agent/docker-compose.legacy.yml build chat-web
agent_compose up -d --wait --wait-timeout 75
agent_compose ps --all
```

若没有任何批准依赖，本节暂停；使用第一节合成演练，不把 synthetic overlay 加到此项目。
新 runtime 卷由 `state-init` 以 UID 10001 初始化，API 单 worker 并持有整库租约。
健康探测只确认启动边界，仍需在批准范围内验证能力、双访客、JSON / SSE 与静态资源。
本阶段没有执行上面这套真实依赖启动命令。

## 3. 停服、备份、新卷切换

操作前核对项目名、镜像 ID、受控存储 profile、恢复点、Origin / 密钥备份及实际写入方。
以下示例目标 `release-001` 与 restored `store` 必须从未创建；CLI 拒绝覆盖。二次演练
使用另一新目录和相应恢复 overlay / 卷，不删除旧目标来“让命令成功”。

```bash
agent_compose stop chat-web assistant-api
agent_compose run --rm --no-deps storage -m spb_assistant_api.storage_cli backup --source /var/lib/spb-runtime/store --destination /var/lib/spb-backups/release-001
agent_compose run --rm --no-deps storage -m spb_assistant_api.storage_cli verify --source /var/lib/spb-backups/release-001
agent_compose run --rm --no-deps storage -m spb_assistant_api.storage_cli restore --source /var/lib/spb-backups/release-001 --destination /var/lib/spb-restored/store
agent_compose -f deploy/agent/docker-compose.restored.yml up -d --wait --wait-timeout 75
```

每一步成功后才执行下一步，失败保持停服并调查。`store_busy` 先查写入进程；
`unfinished_requests` 先核对请求 / 收据，不删 claim 后重试；`target_exists` 换全新目标。
恢复后需先核验原 owner、原 TTL、已完成请求免执行回放、暂停请求继续、跨 owner 拒绝
与删除，再决定是否对外恢复服务。备份恢复不包含密钥，不自动调用供应商来证明有效。

示例备份源为初始 runtime；切至 restored 后，后续备份源也必须显式改为
`/var/lib/spb-restored/store`。绝不可一边从旧库备份、一边宣称已经备份新运行库。
恢复可能带回快照之后删除的数据；真实恢复需核对外部删除记录，目前没有自动删除账本。

## 4. V1 API/UI 回退与返回 V2

在第三节已切换 restored 卷的前提下，停服后同时改变 API 与 Web，保留 restored overlay：

```bash
agent_compose -f deploy/agent/docker-compose.restored.yml stop chat-web assistant-api
agent_compose -f deploy/agent/docker-compose.restored.yml -f deploy/agent/docker-compose.legacy.yml up -d --wait --wait-timeout 75
```

此时只开放 V1 chat，V2 404；Agent DB 不打开、不降级、不删除。V1 health 使用 liveness，
不代表 RAG / MySQL 可用，需另行验证批准能力。核验回退后，再停服、撤销 legacy overlay：

```bash
agent_compose -f deploy/agent/docker-compose.restored.yml -f deploy/agent/docker-compose.legacy.yml stop chat-web assistant-api
agent_compose -f deploy/agent/docker-compose.restored.yml up -d --wait --wait-timeout 75
```

若从未恢复，则整个回退 / 返回过程都省略 restored overlay；不要切到不存在的恢复库。
代理运行模式必须与 Web 镜像内的模式一致，否则启动失败。此操作只验证当前版本切换，
不能用旧版镜像打开新 schema；真正的版本升级 / 回滚需单独迁移验收与不可变镜像。

## 5. 保留资源与后续验收

- 合成脚本保留本次 `_runtime-data`、`_backup-data`、`_restored-data`、`_synthetic-tls`
  实际创建的卷。确认只含本次合成数据后，由操作者选择是否清理；没有自动删卷策略。
- 不运行默认项目的 `down -v`，不覆盖旧库或改已有目录权限，也不删除其他阶段的演练卷。
- 固定 digest 的依赖镜像仍需维护补丁；本地 API 为 amd64、Web 随基础镜像平台构建，
  不能据本机结果宣称多架构与性能合格。应用 tag 可变，报告记录 ID 不等于已发布版本。
- CI 文件已提供，尚无远程执行证据；Git 操作、业务访问、公开部署必须另有授权。
- [工具链收口](../../docs/agent-kernel-phase6a3-ci-closeout.md)已升级 Vitest 4.1.11，默认
  `npm test` 不加载 dotenv，CI 显式包含 dev / 拦截 moderate，固定 Node 22 构建内测试通过。
  当前 npm audit 为 0，Python / OS 扫描仍待补。下一步授权后提交推送、验证远程 CI，
  再按资料 / 业务授权推进真实接口与 holdout。
