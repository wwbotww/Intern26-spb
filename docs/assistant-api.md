# 中国邮政理赔助手 API 使用文档

本文面向调用 `assistant-api` 的 Web 或其他咨询客户端。当前版本为 `0.3.1`，
提供显式政策/设备价格二选一、单轮问答、结构化证据和 SSE 输出。

该服务是咨询入口，不执行理赔审批、赔付计算、案件流转或数据库写入。每次请求
相互独立，不接收会话历史，也不会自动融合政策和价格结果。

## 1. 接入与鉴权

调用方从服务管理员处取得：

```text
Base URL: https://<assistant-api-address>
API Key:  <assistant-service-key>
```

业务接口使用以下任一请求头：

```http
Authorization: Bearer <assistant-service-key>
X-API-Key: <assistant-service-key>
```

同源 `chat-web` 部署由 Nginx 在服务端注入 Key，浏览器只调用 `/api`，不应拿到
Assistant Key、RAG Key、MySQL DSN 或模型凭证。`X-Request-ID` 可由调用方传入，
格式为 1～64 个字母、数字、下划线或连字符；响应始终返回最终 Request ID。

## 2. 接口总览

| 方法 | 路径 | 用途 | 鉴权 |
|---|---|---|---|
| `POST` | `/v1/chat` | 政策或设备价格咨询，支持 JSON/SSE | 是 |
| `GET` | `/health/live` | 进程存活和静态协议状态 | 否 |
| `GET` | `/health/ready` | 两个工具和运行依赖是否就绪 | 否 |
| `GET` | `/metrics` | Prometheus 指标 | 否，仅限运维网络 |
| `GET` | `/docs` | OpenAPI 交互文档 | 由部署策略决定 |

## 3. `POST /v1/chat`

请求体只有三个字段，额外字段会被拒绝：

```json
{
  "mode": "policy",
  "question": "快递业务经营许可需要符合哪些条件？",
  "stream": false
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `mode` | string | 是 | 只允许 `policy` 或 `device_price` |
| `question` | string | 是 | 去除首尾空格后长度 1～2000 |
| `stream` | boolean | 否 | 默认 `true`；`false` 返回完整 JSON |

请求不得携带 `history`、`messages`、`session_id` 或工具名。一个请求只执行
`mode` 对应的一个只读工具；同时询问政策和价格时，服务要求拆成两次请求。

### 3.1 非流式响应

HTTP 200：

```json
{
  "request_id": "generated-request-id",
  "mode": "device_price",
  "answer": "查询到多个可能匹配的设备参考价格记录。",
  "evidence": [
    {
      "evidence_id": "price-1",
      "type": "device_price",
      "title": "示例设备",
      "brand": "示例品牌",
      "model": "示例型号",
      "specification": "256GB",
      "price": "0.00",
      "currency": "CNY",
      "source": "官方渠道",
      "observed_at": "2026-01-01T00:00:00Z",
      "availability": "ON_SALE",
      "source_url": "https://example.invalid/item",
      "original_price": null,
      "original_price_type": "",
      "official_product_id": "example-product",
      "official_sku_id": "example-sku",
      "match_score": 90.0
    }
  ],
  "warnings": ["同一型号可能包含多个 SKU，请结合规格确认。"],
  "missing_fields": [],
  "used_tool": "device_price",
  "finish_reason": "stop",
  "reason_code": "",
  "usage": {}
}
```

示例值只说明协议，不代表真实设备或价格。

公共字段：

| 字段 | 说明 |
|---|---|
| `answer` | 已通过证据约束的回答或固定提示 |
| `evidence` | 政策或价格证据；非回答结果必须为空 |
| `warnings` | 多 SKU、截断、非在售等限制提示 |
| `missing_fields` | 建议下次独立请求补充的字段 |
| `used_tool` | `policy_knowledge` 或 `device_price` |
| `finish_reason` | 统一结束状态 |
| `reason_code` | 数据源或门槛的具体原因，可为空 |
| `usage` | 发生模型调用时的用量；价格模板通常为空 |

政策证据字段为 `title`、`source_url`、`excerpt`、`document_no`、
`published_at`、`source_org`、`section_path`、`chunk_id`、`document_id`、
`score` 和 `rerank_score`。价格证据字段见上例。客户端应按 `type` 分支渲染，
不能从回答 Markdown 反向解析事实。

### 3.2 结束状态

| `finish_reason` | 含义 | 客户端建议 |
|---|---|---|
| `stop` | 有足够证据并完成回答 | 展示回答和证据 |
| `partial` | 证据只支持部分问题 | 展示支持部分及警告 |
| `insufficient_information` | 缺少型号/规格或问题跨类别 | 展示 `missing_fields` |
| `no_match` | 当前数据源无可靠匹配 | 不生成推测事实 |

常见 `reason_code`：

- `no_context`、`reranker_rejected`、`llm_rejected`：政策资料被对应门槛拒绝；
- `multiple_query_categories`：同一问题同时包含政策和价格需求；
- `stop`：政策链路正常完成；
- 空字符串：价格模板结果没有更细的门槛原因。

### 3.3 设备价格匹配语义

设备价格查询采用确定性的两阶段匹配，不使用 LLM 或向量相似度决定产品：

1. 从本次问题中提取品牌提示、产品系列、型号和容量；
2. MySQL 只在产品名、系列名和型号字段中粗召回；
3. 按 `official_product_id` 聚合产品，不使用 SKU 名、内存、尺寸或 SKU ID
   判断产品身份；
4. 产品系列、型号数字、字母数字型号和明确的
   `Pro/Max/Plus/Ultra/SE` 版本词必须与产品级字段一致；
5. 硬约束通过后才使用 RapidFuzz 产品名称分数和配置阈值，只保留最高分的
   产品分组；
6. 产品可信后，在该产品内部按容量/内存筛选并返回多个 SKU 价格。

因此，相似但型号数字、产品系列或明确版本不同的设备不会作为替代价格返回。
没有可信产品时返回 `no_match`；只有容量、品牌或版本词而没有有效产品身份时
返回 `insufficient_information`，且不会执行全库价格匹配。拼写错误当前可能
得到 `no_match`，客户端应提示用户核对完整型号，不应自动选择“最像”的产品。

价格结果中的 `match_score` 是硬约束通过后的产品名称相似度，只用于候选排序，
不能单独解释为概率或最终定损置信度。

### 3.4 SSE

```bash
curl -N -X POST "${ASSISTANT_BASE_URL}/v1/chat" \
  -H "Authorization: Bearer ${ASSISTANT_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "policy",
    "question": "快递业务经营许可需要符合哪些条件？",
    "stream": true
  }'
```

正常事件顺序：

```text
status -> evidence -> delta -> [usage] -> done
```

- `status` 仅表示“正在查询”，不包含模型思维链；
- `evidence.items` 是完整结构化证据数组，也可能为空；
- `delta.content` 是回答内容。政策回答当前先完成引用校验再发送，通常只有一个
  `delta`，不承诺逐 Token；
- `usage` 仅在上游返回用量时出现；
- `done` 包含 `request_id`、`mode`、`used_tool`、结束/原因、警告和缺失字段；
- 建立流后的技术失败只发送 `error`，不再发送 `done`。

客户端必须按事件边界解析，不能依赖 TCP 分片或每行对应一个完整响应。

## 4. 信息不足示例

请求：

```json
{"mode":"device_price","question":"这个设备多少钱？","stream":false}
```

关键响应：

```json
{
  "evidence": [],
  "missing_fields": ["brand_or_model"],
  "used_tool": "device_price",
  "finish_reason": "insufficient_information"
}
```

客户端应提示用户在新的独立请求中写明品牌、完整型号和必要规格，不能把页面上
一轮消息作为隐式上下文补回服务端。

## 5. 健康检查

`/health/live` 只表示进程存活，并返回 `routing=explicit`、支持的两种模式和
`memory=disabled`。

`/health/ready` 只有在以下检查都可接受时返回 HTTP 200：

- `policy_knowledge=ready`：RAG ready 且内部 RAG Key 有效；
- `device_price=ready`：MySQL 连接和只读 schema 映射可用；
- `auth=ready`；
- `metrics=ready` 或明确 `disabled`；
- `routing=explicit`、`memory=disabled`。

任一工具或依赖不可用时返回 HTTP 503；调用方不能把 503 解释成“没有业务数据”。

## 6. HTTP 错误

| HTTP | `detail.code` | 含义 |
|---:|---|---|
| 401 | `unauthorized` | 缺少或无效服务 Key |
| 413 | `request_too_large` | 请求体超过配置上限 |
| 422 | FastAPI 校验错误 | mode、问题长度、类型或额外字段不合法 |
| 429 | `rate_limit_exceeded` | 超过 Key 级滑动窗口限流 |
| 502 | `tool_failed` / `tool_contract_failed` | 工具执行或返回契约异常 |
| 503 | `auth_not_configured` / `tool_unavailable` | 服务鉴权或依赖未就绪 |

429 响应带 `Retry-After`；通过鉴权的业务请求通常带
`X-RateLimit-Limit` 和 `X-RateLimit-Remaining`。调用方应保存 HTTP 状态、
`X-Request-ID` 和错误码，不记录 API Key、完整内部配置或不必要的业务内容。

## 7. 使用边界

- 所有价格都是数据库观察到的参考信息，不是定损价或赔付结论；
- 政策回答必须与返回的公开原文证据一起展示并允许用户核验；
- `no_match`、信息不足和技术错误必须使用不同 UI 状态；
- 当前没有服务端记忆、自动意图识别、多工具融合或内部审批动作；
- 正式法律、行政或理赔决定仍应由主管机构和人工流程确认。
