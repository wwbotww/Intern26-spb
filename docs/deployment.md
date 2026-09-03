# Docker Compose 部署与运行

> 适用基线：`chat-web 0.2.0`、`assistant-api 0.3.2`、`rag-api 0.5.1`。
>
> 本文只描述仓库当前的通用部署方式，不记录具体客户、主机、内网地址或密钥。

## 1. 运行拓扑

```text
Browser
  -> 127.0.0.1:3000 chat-web / Nginx
       -> assistant-api:8081
            -> rag-api:8080 -> Milvus + DeepSeek
            -> MySQL (read-only account)

Optional Prometheus -> rag-api / assistant-api metrics
```

`deploy/docker-compose.yml` 当前包含：

| 服务 | 镜像标签 | 主机绑定 | 说明 |
| --- | --- | --- | --- |
| `chat-web` | `intern26-spb-chat-web:0.2.0` | `127.0.0.1:3000` | Nginx 托管静态页面并反向代理 `/api/` |
| `assistant-api` | `intern26-spb-assistant-api:0.3.2` | `127.0.0.1:8081` | 单轮工具编排入口 |
| `rag-api` | `intern26-spb-rag-api:0.5.1` | `127.0.0.1:8080` | 政策检索与回答 |
| `prometheus` | `prom/prometheus:v3.5.0` | `127.0.0.1:9091` | 可选 `monitoring` profile |

所有端口默认只绑定 loopback。需要远程访问时应由反向代理、TLS 和防火墙显式暴露 Web；不要直接把 RAG、Assistant 或 `/metrics` 暴露到公网。

离线采集和 Milvus 写入不属于在线 Compose。生产 Milvus 账号应分别授予：离线流水线写权限、RAG 服务只读权限。

## 2. 前置条件

- Docker 与支持 Compose v2 的 `docker compose`。
- 可访问目标 Milvus、MySQL 和模型供应商 API 的网络。
- 建议至少为 RAG 容器预留 4 CPU、6 GiB 内存；实际容量需按目标机器压测。
- 构建阶段需要下载 embedding 和 reranker 权重；构建完成后 RAG 容器以 Hugging Face 离线模式运行。

## 3. 配置

### 3.1 RAG 配置

创建不入 Git 的服务配置：

```bash
cp apps/rag-api/.env.example apps/rag-api/.env
chmod 600 apps/rag-api/.env
```

至少设置：

```text
RAG_MILVUS_URI=
RAG_MILVUS_TOKEN=
RAG_DEEPSEEK_API_KEY=
RAG_API_KEYS=<random-rag-service-key>
```

在线进程应使用 Milvus 只读账号。`RAG_API_KEYS` 是调用 RAG 的服务密钥，不是 DeepSeek Key；可用逗号并存新旧 Key 以支持轮换。

### 3.2 Compose / Assistant / Web 配置

在仓库根目录创建被 Git 忽略的 `.env`，或使用 `--env-file` 指向等价的未跟踪文件：

```text
ASSISTANT_API_KEYS=<random-assistant-service-key>
ASSISTANT_RAG_API_KEY=<same-as-one-RAG_API_KEYS-value>
ASSISTANT_MYSQL_DSN=mysql+pymysql://readonly_user:<url-encoded-password>@<host>:3306/<database>?charset=utf8mb4
CHAT_WEB_ASSISTANT_API_KEY=<same-as-one-ASSISTANT_API_KEYS-value>
```

可选调节项在 `deploy/docker-compose.yml` 和两个 API 的 `.env.example` 中有完整默认值。重要关系：

- `ASSISTANT_RAG_BASE_URL` 在容器内默认是 `http://rag-api:8080`。
- `CHAT_WEB_ASSISTANT_API_KEY` 由 Nginx 注入内部请求，浏览器不应获得该 Key。
- MySQL 账号只授予所需表的 `SELECT` 权限；密码中的特殊字符必须 URL 编码。
- 鉴权开启但没有配置有效 Key 时，服务按 fail-closed 原则不进入可用状态。

不要提交 `.env`，也不要把包含展开后环境变量的 `docker compose config` 完整输出复制到公共日志。

## 4. 构建与启动

从仓库根目录运行：

```bash
docker compose -f deploy/docker-compose.yml build rag-api assistant-api chat-web
docker compose -f deploy/docker-compose.yml up -d rag-api assistant-api chat-web
docker compose -f deploy/docker-compose.yml ps
```

如果使用单独配置文件：

```bash
docker compose --env-file /path/to/untracked/runtime.env \
  -f deploy/docker-compose.yml up -d --build
```

`assistant-api` 等待 `rag-api` 的容器健康检查；`chat-web` 等待 `assistant-api`。容器 healthcheck 使用 `/health/live`，避免临时上游故障造成无意义重启；流量入口应使用 `/health/ready` 判断是否接流量。

## 5. 验证

```bash
curl --fail http://127.0.0.1:8080/health/live
curl --fail http://127.0.0.1:8080/health/ready
curl --fail http://127.0.0.1:8081/health/live
curl --fail http://127.0.0.1:8081/health/ready
curl --fail http://127.0.0.1:3000/
```

含义：

- RAG `live`：进程与静态 collection / embedding 契约可响应。
- RAG `ready`：retriever、Milvus、模型供应商、evidence judge、鉴权和监控配置满足要求。
- Assistant `live`：进程与当前显式路由协议可响应。
- Assistant `ready`：政策工具、设备价格工具、鉴权和监控配置满足要求；任一必需依赖未就绪时返回 503。

浏览器访问 `http://127.0.0.1:3000`。业务 API 也可以通过 Web 同源前缀 `/api/v1/chat` 调用；生产入口应以反向代理配置为准。

## 6. 日志、停止与更新

查看日志：

```bash
docker compose -f deploy/docker-compose.yml logs -f --tail=100 chat-web assistant-api rag-api
```

停止：

```bash
docker compose -f deploy/docker-compose.yml down --timeout 30
```

更新时使用不可变版本标签构建并验证新镜像，保留上一组已验证镜像标签作为回滚基线。回滚应重新部署旧标签和与之匹配的配置，而不是保留命名不明确的停止容器。

RAG 和 Assistant 输出结构化日志，使用 request ID / trace 信息关联跨服务请求。日志不得记录问题正文、数据库 DSN、Milvus Token、服务 API Key 或模型凭据。

## 7. 监控

启动可选 Prometheus：

```bash
docker compose -f deploy/docker-compose.yml --profile monitoring up -d
```

Prometheus 只绑定 `127.0.0.1:9091`。重点观察：

- 请求量、状态码、in-flight、P50 / P95 / P99。
- 鉴权失败、限流拒绝和请求体拒绝。
- 检索、reranker、evidence judge、生成与工具调用分阶段耗时。
- DeepSeek token、拒答原因和 reranker 分数分布。
- Assistant 的路由、工具状态和上游错误。

`/metrics` 无业务 API Key，必须只在运维网络可达。

## 8. 安全边界

- RAG 与 Assistant 镜像使用 UID 10001 非 root 用户。
- API 容器使用只读根文件系统、`tmpfs /tmp`、移除全部 Linux capabilities，并启用 `no-new-privileges`。
- RAG 和 Assistant 使用不同服务密钥；Key 只存于运行时配置。
- Web 容器代替浏览器持有 Assistant Key；对外入口必须配置 TLS、Host 校验和安全响应头。
- 设备价格库和 Milvus 使用独立只读账号；离线写权限不进入在线容器。
- 私有评测集、模型回答、原始附件和运行日志不进入镜像或 Git。

## 9. 当前容量与功能边界

- RAG 默认 `RAG_RERANK_MAX_CONCURRENCY=1`；CPU reranker 在并发下会排队，应在目标硬件上压测后调整。
- 两个 API 的限流器是进程内实现，只适合单 worker、单副本。横向扩展时应在 API Gateway 或 Redis 实现全局限流。
- Assistant 的 MySQL 查询通过只读连接池运行；连接池和查询 timeout 需要结合数据库容量标定。
- 当前 Assistant 使用显式 `policy` / `device_price`、单轮请求且无服务端会话记忆。
- Assistant 当前只支持显式模式、单轮、单工具查询；本文不对尚未确认的后续能力作部署假设。

## 10. 本地开发入口

API 开发方式见根目录 README 和对应 API 文档。Chat Web 本地启动：

```bash
cp apps/chat-web/.env.example apps/chat-web/.env
cd apps/chat-web
npm ci
npm run dev
```

默认开发代理连接 `http://127.0.0.1:8081`，因此需先启动并配置 `assistant-api`。完整接口行为见 [Assistant API](assistant-api.md) 和 [RAG API 调用契约](api-reference.md)。
