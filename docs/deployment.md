# Linux / Docker 部署

当前 CentOS 7 Demo 服务器的实际部署记录、访问方式和运维命令见
[Demo 服务器部署简要说明](server-deployment-brief.md)。本文其余内容描述标准
Docker Compose 部署方式。

## 运行边界

在线服务镜像只包含：

- `spb-rag-api`；
- `spb-contracts`；
- Python 运行依赖；
- `moka-ai/m3e-base` 模型权重；
- `BAAI/bge-reranker-base` 重排序模型权重。

离线抓取代码、测试、业务数据、`.env`、Git 历史和本地缓存不会进入镜像。
容器以 UID 10001 非 root 用户运行，根文件系统只读，移除全部 Linux
capabilities，并启用 `no-new-privileges`。

## 配置

从示例创建本地配置：

```bash
cp apps/rag-api/.env.example apps/rag-api/.env
chmod 600 apps/rag-api/.env
```

至少填写：

```text
RAG_MILVUS_URI=
RAG_MILVUS_TOKEN=
RAG_DEEPSEEK_API_KEY=
RAG_API_KEYS=
```

`RAG_API_KEYS` 是本服务调用方使用的 API Key，不是 DeepSeek Key。可以用逗号
配置多个值，以支持无停机轮换：

```text
RAG_API_KEYS=new-key,old-key
```

所有业务接口默认 fail closed：启用鉴权但没有配置 `RAG_API_KEYS` 时返回 503。
客户端使用任一请求头：

```text
Authorization: Bearer <service-api-key>
X-API-Key: <service-api-key>
```

不要提交 `.env`，也不要把 `docker compose config` 的完整输出粘贴到日志或
工单中，因为 Compose 会展开 `env_file` 的敏感值。

## 构建和启动

```bash
docker compose -f deploy/docker-compose.yml build rag-api
docker compose -f deploy/docker-compose.yml up -d rag-api
docker compose -f deploy/docker-compose.yml ps
```

embedding 和 reranker 模型均在构建阶段下载并写入镜像；运行时启用
Hugging Face 离线模式，不依赖公网模型仓库。Linux 锁文件将 PyTorch 固定到
官方 CPU wheel index，避免将 CUDA runtime 打入 CPU 服务镜像。

查看日志：

```bash
docker compose -f deploy/docker-compose.yml logs -f --tail=100 rag-api
```

优雅停止：

```bash
docker compose -f deploy/docker-compose.yml down --timeout 30
```

## 健康检查

```bash
curl http://127.0.0.1:8080/health/live
curl http://127.0.0.1:8080/health/ready
```

- `live` 只判断进程是否存活；
- `ready` 检查 embedding、Milvus、DeepSeek、鉴权和监控配置。

镜像和 Compose healthcheck 使用 `live`，避免临时上游故障触发容器重启；
流量入口应使用 `ready` 决定是否转发业务请求。

## 重排序和相关性门槛

```text
RAG_RERANK_ENABLED=true
RAG_RERANK_MODEL=BAAI/bge-reranker-base
RAG_RERANK_FETCH_K=20
RAG_RERANK_BATCH_SIZE=8
RAG_RERANK_MIN_SCORE=0.5
RAG_RERANK_SHADOW_MODE=false
RAG_RELEVANCE_JUDGE_ENABLED=true
RAG_RELEVANCE_JUDGE_MAX_SOURCES=5
```

`RAG_RERANK_MIN_SCORE=0.5` 仅为 Demo 初始值。上线评测前应先使用
`RAG_RERANK_SHADOW_MODE=true` 收集可回答、不可回答和 topic-only 问题的
分数，再标定阈值。Judge 使用独立 DeepSeek JSON 请求，只在其通过后调用答案
生成。

## 限流和并发

```text
RAG_MAX_CONCURRENCY=5
RAG_RATE_LIMIT_ENABLED=true
RAG_RATE_LIMIT_REQUESTS=60
RAG_RATE_LIMIT_WINDOW_SECONDS=60
```

并发信号量覆盖 embedding、Milvus 和完整 DeepSeek SSE 生命周期。滑动窗口
限流按服务 API Key 计算；关闭鉴权时按客户端 IP 计算。

当前限流器是进程内实现，适用于单 worker、单副本部署。横向扩容时应在 API
Gateway 或 Redis 中实现全局限流，不能依赖各副本的本地计数。

## Prometheus

`/metrics` 不需要 API Key，必须仅通过内网或防火墙暴露。启动自带的可选
Prometheus：

```bash
docker compose \
  -f deploy/docker-compose.yml \
  --profile monitoring \
  up -d
```

Prometheus 只绑定本机 `127.0.0.1:9091`。主要指标：

- `spb_http_requests_total`；
- `spb_http_request_duration_seconds`；
- `spb_http_requests_in_flight`；
- `spb_auth_failures_total`；
- `spb_rate_limit_rejections_total`；
- `spb_deepseek_tokens_total`；
- `spb_deepseek_relevance_judge_tokens_total`；
- `spb_reranker_decisions_total`、`spb_reranker_duration_seconds`；
- `spb_reranker_top_score`；
- `spb_relevance_judge_decisions_total`；
- `spb_relevance_judge_duration_seconds`。

## 结构化日志

默认输出 JSON，每个请求包含：

- UTC 时间；
- level 和 logger；
- request ID；
- HTTP method、route 和 status；
- 完整 SSE 生命周期耗时；
- API Key 的不可逆短哈希 client ID。

请求正文、Milvus Token、服务 API Key 和 DeepSeek Key 不会写入日志。

## 压测

默认压测只调用 `/v1/retrieve`，不会产生 DeepSeek 费用：

```bash
export SPB_RAG_API_KEY='<service-api-key>'
uv run --package spb-rag-api python \
  apps/rag-api/scripts/load_test.py \
  --requests 20 \
  --concurrency 5
```

输出包含成功数、状态码分布、吞吐量以及 min/mean/p50/p95/max 延迟。只有明确
传入 `--endpoint chat` 才会调用 DeepSeek 并产生费用。
