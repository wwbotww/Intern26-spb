# 国家邮政局政策知识库 API 使用文档

本文档面向调用国家邮政局政策法规标准知识库服务的应用开发者。服务提供：

- 政策知识库混合检索；
- 基于检索资料的 DeepSeek 问答；
- JSON 非流式响应；
- Server-Sent Events（SSE）流式响应。

当前 API 主版本为 `v1`。

## 1. 接入信息

请向服务管理员获取以下信息：

```text
Base URL: https://<服务地址>
API Key:  <服务调用密钥>
```

API Key 是本知识库服务的访问凭证，不是 DeepSeek API Key。调用方不需要、
也不应持有服务端使用的 DeepSeek API Key 或 Milvus 凭证。

建议在客户端环境变量中保存接入信息：

```bash
export SPB_RAG_BASE_URL='https://<服务地址>'
export SPB_RAG_API_KEY='<服务调用密钥>'
```

除健康检查和监控端点外，所有接口都需要以下任一鉴权请求头：

```http
Authorization: Bearer <服务调用密钥>
```

或：

```http
X-API-Key: <服务调用密钥>
```

推荐使用 `Authorization: Bearer`。不要把 API Key 放在 URL、查询参数、日志或
前端公开代码中。

## 2. 通用约定

### 2.1 请求与编码

- JSON 请求使用 UTF-8；
- `Content-Type` 使用 `application/json`；
- 默认请求体上限为 1 MiB，具体值以部署配置为准；
- 日期请求字段使用 `YYYY-MM-DD`；
- 未声明为可空的字符串不能是空字符串。

### 2.2 Request ID

调用方可以传入：

```http
X-Request-ID: order-service-20260729-0001
```

允许的格式为 1～64 个字母、数字、下划线或连字符。未传入或格式不合法时，
服务会生成 32 位 Request ID。

所有 HTTP 响应均返回 `X-Request-ID`。问答 JSON 响应及 SSE
`metadata`、`done`、`error` 事件中的 `request_id` 与该响应头一致。排查问题
时请记录此值。

### 2.3 限流响应头

业务接口成功通过鉴权后可能返回：

```http
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 58
```

超过限制时返回 HTTP 429，并额外提供：

```http
Retry-After: 42
```

默认限制为每个 API Key 每 60 秒 60 次请求，实际限制以部署环境为准。

### 2.4 接口总览

| 方法 | 路径 | 用途 | 需要鉴权 |
|---|---|---|---|
| `POST` | `/v1/retrieve` | 混合检索，返回原始知识片段 | 是 |
| `POST` | `/v1/chat` | 检索增强问答，支持 JSON/SSE | 是 |
| `GET` | `/health/live` | 进程存活检查 | 否 |
| `GET` | `/health/ready` | 上游依赖就绪检查 | 否 |
| `GET` | `/metrics` | Prometheus 指标 | 否，仅限运维网络 |

## 3. 混合检索

### `POST /v1/retrieve`

使用 `moka-ai/m3e-base` 稠密向量和 BM25 稀疏检索召回候选，再使用 RRF
融合排序。该接口不调用 DeepSeek。

### 3.1 请求

```bash
curl -sS -X POST "${SPB_RAG_BASE_URL}/v1/retrieve" \
  -H "Authorization: Bearer ${SPB_RAG_API_KEY}" \
  -H 'Content-Type: application/json' \
  -H 'X-Request-ID: retrieve-example-001' \
  -d '{
    "query": "快递业务经营许可需要符合哪些条件？",
    "top_k": 5,
    "candidate_k": 40,
    "filters": {
      "document_types": ["html", "pdf"],
      "validity_statuses": ["有效", "unknown"],
      "source_orgs": ["国家邮政局"],
      "published_from": "2018-01-01",
      "published_through": "2026-12-31"
    }
  }'
```

请求字段：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| `query` | string | 是 | — | 查询文本，去除首尾空格后长度 1～2000 |
| `top_k` | integer | 否 | 8 | 最终返回数量；默认服务上限为 20 |
| `candidate_k` | integer | 否 | 40 | 稠密、稀疏检索各自召回的候选数 |
| `filters` | object | 否 | `{}` | 结构化过滤条件 |

`candidate_k` 必须大于等于 `top_k`，默认服务上限为 100。接口模型允许的
绝对上限分别为 `top_k <= 100`、`candidate_k <= 500`，但部署环境可以设置更
严格的服务上限。

过滤字段：

| 字段 | 类型 | 最大数量 | 说明 |
|---|---|---:|---|
| `document_types` | string[] | 10 | 文档类型精确匹配 |
| `validity_statuses` | string[] | 10 | 有效性精确匹配，例如 `有效`、`unknown` |
| `source_orgs` | string[] | 20 | 发布机构精确匹配 |
| `published_from` | date | — | 发布日期下界，包含当天 |
| `published_through` | date | — | 发布日期上界，包含当天 |

同一数组内的值按“或”处理，不同过滤字段之间按“且”处理。每个数组元素去除
首尾空格后长度为 1～256。`published_from` 不能晚于
`published_through`。

调用方只能使用上述字段，不能直接提交 Milvus filter 表达式。

### 3.2 成功响应

HTTP 200：

```json
{
  "query": "快递业务经营许可需要符合哪些条件？",
  "mode": "hybrid_rrf",
  "count": 1,
  "elapsed_ms": 96.314,
  "results": [
    {
      "rank": 1,
      "score": 0.032,
      "chunk_id": "chunk-id",
      "document_id": "document-id",
      "parent_document_id": "",
      "title": "政策文件标题",
      "text": "命中的政策正文片段",
      "source_url": "https://www.spb.gov.cn/example.shtml",
      "document_type": "html",
      "published_at": "2025-01-01 00:00:00",
      "document_no": "文件文号",
      "source_org": "发布机构",
      "validity_status": "有效",
      "section_path": "第二章/第十条",
      "chunk_index": 3
    }
  ]
}
```

响应字段说明：

| 字段 | 说明 |
|---|---|
| `mode` | 固定为 `hybrid_rrf` |
| `count` | 本次实际返回的结果数，可能小于 `top_k` |
| `elapsed_ms` | 服务端 embedding 和 Milvus 检索耗时 |
| `rank` | 从 1 开始的本次结果排序 |
| `score` | RRF 融合分数，越高表示本次查询中的综合排名越靠前 |
| `chunk_id` | 知识片段唯一标识 |
| `document_id` | 来源文档标识 |
| `parent_document_id` | 父文档标识；没有时为空字符串 |
| `text` | 命中的知识片段正文 |
| `source_url` | 国家邮政局原始来源地址 |
| `section_path` | 片段在文档中的章节位置 |
| `chunk_index` | 片段在文档中的顺序 |

RRF `score` 不是相似度百分比，不应转换为置信度，也不建议跨不同查询直接
比较。部分历史文件可能缺少文号、发布机构等元数据，对应字段会是空字符串。

## 4. 检索增强问答

### `POST /v1/chat`

服务先执行与 `/v1/retrieve` 相同的混合检索，再让 DeepSeek 仅依据检索结果
回答，并要求关键结论使用 `[1]`、`[2]` 等编号引用来源。

`stream` 决定响应协议：

- `false`：返回一个完整 JSON；
- `true` 或省略：返回 SSE 流。

### 4.1 请求

```json
{
  "question": "快递业务经营许可需要符合哪些条件？",
  "stream": false,
  "top_k": 5,
  "candidate_k": 40,
  "filters": {
    "validity_statuses": ["有效", "unknown"]
  }
}
```

字段说明：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| `question` | string | 是 | — | 用户问题，长度 1～2000 |
| `stream` | boolean | 否 | `true` | 是否使用 SSE 流式响应 |
| `top_k` | integer | 否 | 8 | 提供给问答上下文的最大检索结果数 |
| `candidate_k` | integer | 否 | 40 | 每一路检索候选数 |
| `filters` | object | 否 | `{}` | 与检索接口相同的结构化过滤条件 |

### 4.2 非流式 JSON

请求示例：

```bash
curl -sS -X POST "${SPB_RAG_BASE_URL}/v1/chat" \
  -H "Authorization: Bearer ${SPB_RAG_API_KEY}" \
  -H 'Content-Type: application/json' \
  -H 'X-Request-ID: chat-json-example-001' \
  -d '{
    "question": "快递业务经营许可需要符合哪些条件？",
    "stream": false,
    "top_k": 5
  }'
```

HTTP 200 响应：

```json
{
  "request_id": "chat-json-example-001",
  "model": "deepseek-v4-flash",
  "answer": "根据相关规定，申请人应满足相应条件[1]。",
  "citations": [
    {
      "index": 1,
      "chunk_id": "chunk-id",
      "document_id": "document-id",
      "title": "政策文件标题",
      "source_url": "https://www.spb.gov.cn/example.shtml",
      "document_no": "文件文号",
      "published_at": "2025-01-01 00:00:00",
      "source_org": "发布机构",
      "section_path": "第二章/第十条",
      "score": 0.032,
      "excerpt": "用于展示的来源正文摘要"
    }
  ],
  "usage": {
    "prompt_tokens": 680,
    "completion_tokens": 120,
    "total_tokens": 800
  },
  "finish_reason": "stop"
}
```

`answer` 中的 `[n]` 对应 `citations` 中 `index=n` 的来源。调用方应把引用编号
和来源链接一起展示，不要仅展示模型文本。

`usage` 直接来自模型服务，除三个主要 token 字段外可能包含缓存 token 等扩展
字段，客户端应忽略不认识的字段。

### 4.3 SSE 流式响应

请求示例：

```bash
curl -sS -N -X POST "${SPB_RAG_BASE_URL}/v1/chat" \
  -H "Authorization: Bearer ${SPB_RAG_API_KEY}" \
  -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -H 'X-Request-ID: chat-sse-example-001' \
  -d '{
    "question": "快递业务经营许可需要符合哪些条件？",
    "stream": true,
    "top_k": 5
  }'
```

成功时 HTTP 状态为 200，`Content-Type` 为 `text/event-stream`，并设置：

```http
Cache-Control: no-cache
X-Accel-Buffering: no
```

正常事件顺序：

```text
event: metadata
data: {"request_id":"chat-sse-example-001","model":"deepseek-v4-flash","citations":[...]}

event: delta
data: {"content":"根据相关规定[1]，"}

event: delta
data: {"content":"申请人应满足相应条件。"}

event: usage
data: {"prompt_tokens":680,"completion_tokens":120,"total_tokens":800}

event: done
data: {"finish_reason":"stop","request_id":"chat-sse-example-001"}
```

事件说明：

| 事件 | 次数 | 说明 |
|---|---:|---|
| `metadata` | 1 | 首个业务事件，包含模型名称和完整引用列表 |
| `delta` | 0～N | 回答文本增量，按到达顺序拼接 `content` |
| `usage` | 0～1 | 模型 token 用量 |
| `done` | 1 | 正常结束，包含 `finish_reason` |
| `error` | 0～1 | 流建立后的检索或模型错误；收到后应终止处理 |

服务还可能发送 SSE comment：

```text
: keep-alive
```

调用方应忽略 comment，但用它维持连接活跃。SSE 数据可能被任意拆分到多个网络
数据块中，客户端必须按空行分隔事件，不能假设一次网络读取就是一个完整事件。

浏览器原生 `EventSource` 只支持 GET，不能直接调用本 POST 接口。浏览器客户端
应使用 `fetch` 的 `ReadableStream`，服务端客户端可使用支持流式响应的 HTTP
库。

### 4.4 无匹配资料

没有可用检索资料时，服务不会调用 DeepSeek。

非流式响应仍为 HTTP 200：

```json
{
  "request_id": "request-id",
  "model": "deepseek-v4-flash",
  "answer": "当前知识库资料不足以回答该问题。",
  "citations": [],
  "usage": {},
  "finish_reason": "no_context"
}
```

流式响应依次产生：

1. 引用为空的 `metadata`；
2. 包含固定资料不足提示的 `delta`；
3. `finish_reason=no_context` 的 `done`。

此时不会产生 `usage` 事件。

## 5. 健康检查

### `GET /health/live`

用于判断服务进程是否存活，不代表 Milvus 或 DeepSeek 可用。

```bash
curl -sS "${SPB_RAG_BASE_URL}/health/live"
```

成功时固定返回 HTTP 200：

```json
{
  "status": "ok",
  "service": "spb-rag-api",
  "version": "0.4.0",
  "phase": 4,
  "checks": {
    "workspace": "ok",
    "collection_contract": "spb_policy_chunks:v1",
    "embedding_contract": "moka-ai/m3e-base:768"
  }
}
```

### `GET /health/ready`

用于判断服务是否可以接收完整问答流量。

```bash
curl -sS "${SPB_RAG_BASE_URL}/health/ready"
```

所有依赖就绪时返回 HTTP 200：

```json
{
  "status": "ok",
  "service": "spb-rag-api",
  "version": "0.4.0",
  "phase": 4,
  "checks": {
    "retriever": "ready",
    "embedding": "ready",
    "milvus": "ready",
    "deepseek": "ready",
    "auth": "ready",
    "metrics": "ready"
  }
}
```

任一必需依赖未就绪时返回 HTTP 503，`status` 为 `not_ready`，`checks` 提供
原因。健康端点无需鉴权，但调用方不应高频轮询；建议由网关或编排平台探测。

## 6. 错误处理

### 6.1 业务错误

大多数业务错误使用：

```json
{
  "detail": {
    "code": "unauthorized",
    "message": "缺少或无效的 API Key"
  }
}
```

常见状态码和错误码：

| HTTP | `code` | 含义 | 建议 |
|---:|---|---|---|
| 401 | `unauthorized` | API Key 缺失或无效 | 检查鉴权头，不要自动反复重试 |
| 413 | `request_too_large` | 请求体超过上限 | 缩短问题或过滤条件 |
| 422 | `invalid_search_request` | 检索参数违反服务上限 | 修正 `top_k`、`candidate_k` 等 |
| 422 | `invalid_chat_request` | 问答参数违反服务上限 | 修正请求参数 |
| 422 | `query_too_long` | embedding token 数超过模型上限 | 缩短查询文本 |
| 429 | `rate_limit_exceeded` | 请求超过限流 | 按 `Retry-After` 退避重试 |
| 502 | `retrieval_failed` | Milvus 检索失败 | 使用指数退避有限重试 |
| 502 | `chat_provider_failed` | DeepSeek 调用失败 | 使用指数退避有限重试 |
| 503 | `retrieval_unavailable` | 检索组件未就绪 | 稍后重试并检查 readiness |
| 503 | `chat_provider_unavailable` | 问答模型未配置或未就绪 | 检查 readiness，联系管理员 |
| 503 | `auth_not_configured` | 服务端尚未配置调用方密钥 | 联系管理员 |

请求字段类型、必填项或基础长度校验失败时，FastAPI 返回标准 HTTP 422：

```json
{
  "detail": [
    {
      "type": "missing",
      "loc": ["body", "question"],
      "msg": "Field required",
      "input": {}
    }
  ]
}
```

客户端应优先依据 HTTP 状态和稳定的 `detail.code` 分支处理，不应依赖中文
`message` 文案。

### 6.2 流式错误

SSE 响应头发出后无法再修改 HTTP 状态。因此流建立后的错误通过事件返回：

```text
event: error
data: {"request_id":"request-id","code":"chat_provider_failed","message":"问答模型调用失败"}
```

调用方必须同时处理：

1. 建立流之前返回的非 2xx JSON 错误；
2. HTTP 200 流中的 `error` 事件；
3. 没有 `done` 或 `error` 就意外断开的网络连接。

只有在业务允许重复执行时才重试。重试应复用或关联原 Request ID，并采用带
抖动的指数退避。

## 7. 调用建议

- 普通交互式问答优先使用 SSE，以降低首字等待时间；
- 后台任务或不方便处理流的系统使用 `stream=false`；
- 只需要原始政策片段时调用 `/v1/retrieve`，避免产生模型费用；
- 展示问答结果时同时展示 `citations`，并允许用户打开 `source_url`；
- 不要把 RRF `score` 当作答案正确率；
- 客户端超时应大于服务端模型超时，并为 SSE 配置读取空闲超时；
- 对 429、502、503 进行有限次数退避重试，不要无限重试；
- 保存 `X-Request-ID`、HTTP 状态和错误码，不要记录 API Key 或完整敏感问题；
- `/metrics` 仅供内网监控系统使用，不应暴露到公网。

## 8. OpenAPI

服务运行时提供：

```text
GET /openapi.json
GET /docs
```

这两个路径属于受保护接口，需要有效的服务 API Key。自动生成的 OpenAPI
用于结构和类型校验；SSE 事件顺序、流式错误处理及业务语义以本文档为准。
