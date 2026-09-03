# 在线检索问答 API

本文档说明服务端实现边界和 grounding 规则。面向业务调用方的完整接口契约、
字段、示例、SSE 协议和错误处理见
[`api-reference.md`](api-reference.md)。

> 当前实现版本为 `0.5.1`。本服务是独立、无会话的政策知识工具，不承担其他
> 业务流程假设。

## 边界

在线应用位于 `apps/rag-api`，只读访问 Milvus，不导入离线流水线。它包含两个
彼此独立的入口：

| 接口 | 作用 | 是否调用 DeepSeek |
|---|---|---|
| `POST /v1/retrieve` | 返回 Hybrid/RRF + reranker 结果 | 否 |
| `POST /v1/chat` | 双重相关性门槛后生成带引用回答 | 有候选时先调用 Judge |
| `GET /v1/auth/check` | 轻量验证服务间 API Key | 否 |

没有候选或 reranker 全部拒绝时，`/v1/chat` 固定返回资料不足，不调用
DeepSeek。第一道门槛通过后，DeepSeek Judge 只做 JSON 证据充分性分类；Judge
拒绝时不进入答案生成。

## 双重相关性门槛

1. Dense/BM25 各召回候选并由 RRF 融合；
2. 内部保留最多 `RAG_RERANK_FETCH_K` 条交给
   `BAAI/bge-reranker-base`；
3. reranker 批量重排，低于 `RAG_RERANK_MIN_SCORE` 的候选被过滤；
4. DeepSeek Judge 使用 JSON mode 返回 `answerable` 和认可的来源 ID；
5. 只使用 Judge 认可的来源重新编号、构建 prompt 和 citations；
6. Judge JSON 无效属于技术错误，不伪装成“资料不足”。

`RAG_RERANK_MIN_SCORE=0.5` 是 Demo 初始值，必须通过正例、不可回答问题和
topic-only hard negative 重新标定。可先启用 `RAG_RERANK_SHADOW_MODE=true`
只记录分数而不拦截。

除健康检查和 `/metrics` 外，所有接口默认需要以下任一请求头：

```text
Authorization: Bearer <service-api-key>
X-API-Key: <service-api-key>
```

服务 API Key 由 `RAG_API_KEYS` 配置，与 `RAG_DEEPSEEK_API_KEY` 相互独立。

## 问答请求

```json
{
  "question": "快递业务经营许可需要符合哪些条件？",
  "stream": true,
  "top_k": 5,
  "filters": {
    "document_types": ["html", "pdf"],
    "validity_statuses": ["有效", "unknown"],
    "source_orgs": ["国家邮政局"],
    "published_from": "2018-01-01",
    "published_through": "2026-12-31"
  }
}
```

`top_k`、候选数、查询长度和过滤值数量均受服务端上限约束。客户端不能提交
原始 Milvus filter。

## SSE 协议

流式响应的 `Content-Type` 为 `text/event-stream`，事件顺序如下：

1. `metadata`：请求 ID、模型名称和引用列表；
2. `delta`：回答文本增量；
3. `usage`：DeepSeek 返回的 token 用量；
4. `done`：结束原因；
5. `error`：检索或模型在流建立后失败。

示例：

```text
event: metadata
data: {"request_id":"...","model":"deepseek-v4-flash","citations":[...]}

event: delta
data: {"content":"根据相关规定[1]，"}

event: usage
data: {"prompt_tokens":1200,"completion_tokens":80,"total_tokens":1280}

event: done
data: {"request_id":"...","finish_reason":"stop"}
```

服务透传上游 SSE keep-alive comment，并设置 `X-Accel-Buffering: no`，避免
Nginx 缓冲流式响应。

## Grounding

- 每个检索片段分配稳定的 `[1]`、`[2]` 引用编号；
- 模型只能依据 `<knowledge_base>` 内容回答；
- 来源正文按不可信数据处理，HTML/XML 分隔符会被转义；
- 上下文受 `RAG_CHAT_CONTEXT_MAX_CHARS` 限制；
- 引用元数据独立于模型文本返回，包括标题、文号、机构、日期、章节和原文
  URL；
- 上游错误详情和 API Key 不返回客户端。

## DeepSeek 配置

```text
RAG_DEEPSEEK_BASE_URL=https://api.deepseek.com
RAG_DEEPSEEK_API_KEY=
RAG_DEEPSEEK_MODEL=deepseek-v4-flash
RAG_DEEPSEEK_THINKING=disabled
RAG_DEEPSEEK_TIMEOUT_SECONDS=90
RAG_DEEPSEEK_MAX_TOKENS=1200
RAG_DEEPSEEK_TEMPERATURE=0.1
```

默认关闭思考模式，以降低内部知识问答延迟。若切换为 `enabled`，适配器不会
发送 `temperature`，避免传入思考模式不生效的参数。

## 健康检查

- `/health/live`：进程存活；
- `/health/ready`：retriever、embedding、Milvus、reranker、DeepSeek、证据
  Judge、鉴权与监控配置达到可接受状态。

未配置 DeepSeek 时，纯检索接口仍可使用，但整体 readiness 返回 503，问答
接口返回 `chat_provider_unavailable`。

鉴权、限流、Docker 和监控配置见
[`deployment.md`](deployment.md)。
