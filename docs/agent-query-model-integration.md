# 阶段 2 补齐：真实意图理解模型接入

> 接入里程碑日期：2026-09-07；当时版本：`assistant-api 0.3.4`。
> 当前 `0.3.5` 复用同一 Adapter，并增加本地组件评测，见 [Phase 5C](agent-kernel-phase5c.md)。
>
> Provider Adapter、配置、生命周期和 Mock HTTP / V2 多轮链路已实现。
> 真实 DeepSeek 合成烟测已完成：5 次模型请求，4 次成功、1 次超时后安全回退。
> 代表性质量评测仍未完成，不宣称已验证模型准确率。

## 1. 本阶段交付

原计划的 Hybrid Understanding 已有规则、模型 Port 和 schema gate。本阶段实现第一个
供应商 Adapter，沿用项目 RAG 使用的 DeepSeek，但在 Assistant 内独立装配，不导入
RAG 实现，不隐式读取或复用 RAG 凭据。

```text
V2 message -> HybridQueryUnderstander
                |-- 规则 / 显式入口 / 会话上下文可决策 -> 原流程
                `-- 需要语义补充 -> DeepSeekQueryUnderstandingModel
                                     -> 单次有界 HTTP / JSON object
                                     -> envelope + Pydantic 校验
                                     -> 按选定意图重新执行硬实体提取
                                  -> WorkflowPolicy -> 澄清 / 白名单 Tool / Handoff
```

模型只承担语义分类；Prompt v2 要求 `slots=null`，未知或指代不明时不猜测。
即使模型返回 schema 合法的邮件号等槽位，Hybrid 仍用当前输入重新提取的实体替换，
未提供邮件号就继续补槽。score 是启发式信号，不是校准概率。

## 2. 模块与运行边界

| 文件 | 职责 |
| --- | --- |
| `adapters/deepseek_understanding.py` | wire mapping、单次调用、输出校验、耗时和用量日志 |
| `adapters/agent_http.py` | 复用连接池、TLS、响应大小、错误映射、Request ID 和传输熔断 |
| `query_model.py` | 显式开关与供应商生命周期；关闭时不创建模型 HTTP Client |
| `services/query_understanding.py` | 版本化 Prompt、规则优先、schema gate 与失败回退 |
| `agent_demo.py` | 将可选模型注入已有五能力 fixture / SQLite / V2 lifespan |

`create_query_understander(settings)` 是可复用组合根，产出的 understander 可注入其他
`create_persistent_agent` 装配。默认 `main.app` 与 Compose 仍只发布 V1；Demo 的物流、
政策和价格业务数据仍使用 fixture，模型仅在显式启用后访问外部供应商。

启用模型但 Key、URL 或模型名无效时，配置校验失败；配置有效不代表凭据已获供应商
验证。启动和 readiness 不发送付费探测；模型可选依赖失败时保留规则能力。

## 3. 请求与失败处理

- 固定 `POST chat/completions`，JSON object 模式、非流式、关闭 thinking，设置输出
  Token 上限；不提供 tools/function calling。Prompt 附五意图定义、JSON 示例与领域
  生成的 JSON Schema；JSON mode 不能替代本地 Pydantic 业务校验。
- 默认模型并发上限 2、总预算 8 秒、响应上限 65536 字节。总预算覆盖并发等待和网络；
  每次 Understanding 至多一次模型请求，无模型修复或网络重试，失败由 Hybrid 回退。
- 拒绝非单 choice、非 assistant role、非 stop finish reason、空内容、tool calls、
  非法 JSON、重复字段、非有限数值和领域 schema 错误。取消继续传播并释放容量。
- 共享 HTTP 熔断只统计传输错误，不能代表模型语义准确率。模型调用不是业务 Tool
  调用，不占用 Tool retry 预算，也没有新增模型执行收据；模型响应成功后 checkpoint
  保存前若进程崩溃，重放仍可能产生模型成本，不宣称计费 exactly-once。
- 模型日志只保留 provider、outcome、duration、固定失败分类/代码和合法 token 计数；
  不含问题、Prompt、Key、完整响应或 reasoning_content。此日志不替代后续模型质量
  指标、逐 Node OpenTelemetry 或 Dashboard。

## 4. 配置与真实联调

在被 Git 忽略的 `apps/assistant-api/.env` 中添加下列字段；文件已存在时只合并这些
字段。真实 Key 不写入文档、命令参数、Web 环境变量或聊天记录。

```dotenv
ASSISTANT_QUERY_MODEL_ENABLED=true
ASSISTANT_QUERY_MODEL_BASE_URL=https://api.deepseek.com
ASSISTANT_QUERY_MODEL_NAME=deepseek-v4-flash
ASSISTANT_QUERY_MODEL_API_KEY=<在本地填写你的 DeepSeek Key>
ASSISTANT_QUERY_MODEL_TIMEOUT_SECONDS=8
ASSISTANT_QUERY_MODEL_MAX_TOKENS=768
ASSISTANT_QUERY_MODEL_MAX_RESPONSE_BYTES=65536
ASSISTANT_QUERY_MODEL_MAX_CONCURRENCY=2
```

开启后，fallback 输入会发送给供应商并产生用量。真实联调使用无个人业务信息的合成
语句，此无鉴权 Demo 仅在本地运行：

```bash
ASSISTANT_HOST=127.0.0.1 ASSISTANT_PORT=8081 \
uv run --package spb-assistant-api spb-assistant-agent-demo
```

在 Agent Web 输入“我寄出去的那件东西，眼下在什么地方？”，预期模型识别轨迹并询问
邮件号；随后输入 Demo 邮件号 `1234567890123`，应经规则补槽得到合成轨迹。语义识别
与 V2 补槽链路已在 2026-09-07 经真实模型验证；本次使用 TestClient 调用 ASGI 路由，
未重测浏览器／Nginx，也不代表对其他表达的质量保证。

规则基线必须显式关闭模型：

```bash
ASSISTANT_QUERY_MODEL_ENABLED=false ASSISTANT_HOST=127.0.0.1 \
uv run --package spb-assistant-api spb-assistant-agent-demo
```

对比时使用不同临时 SQLite 数据库（`ASSISTANT_AGENT_DEMO_DB`）和新会话，避免复用旧
幂等响应。模型识别结果可从脱敏语义 Trace 的 `source=model` 与 Prompt 版本确认；
出现 `query_model_call` 日志本身只说明调用过模型，不代表识别成功。

## 5. 验证与后续验收

Mock 测试使用真实 Adapter、HTTPX MockTransport、LangGraph、SQLite 和 V2 API，覆盖
请求/Prompt/schema、规则免模型、未知 fallback、编造槽位覆盖、格式和契约错误、
401/429/5xx、总 deadline、取消、并发、配置开关、资源关闭、补槽恢复与幂等重放。

```bash
ASSISTANT_QUERY_MODEL_ENABLED=false \
.venv/bin/python -m pytest -o addopts='' -q \
  apps/assistant-api/tests/test_query_model_integration.py
```

验收拆为工程接入与 Mock 合同、真实供应商烟测、代表性质量对照三项。当前完成前两项；
第三项不因小样本连通成功而自动完成。

2026-09-07 收口验证：模型集成 `33 passed`，完整 Python workspace `342 passed`；
锁文件离线检查与基础 Ruff 静态检查通过。真实联调后补充三业务多轮、首轮幂等、
unknown／超时安全退出回归，并新增测试配置隔离 fixture 与 1 个验证用例；不修改用户
`.env`，普通 Assistant 测试不读取本地凭据。本阶段未改动 Web 或公开 V2 schema。

真实联调使用四条规则均判 unknown 的合成输入；轨迹／时限／资费识别并补槽完成，
无关输入首次触发 8 秒 deadline，复测在同一预算内正常判 unknown。共 18 次 V2 请求、
5 次模型尝试；7 次幂等重放均无追加模型调用。成功响应已知合计 7998 tokens，超时
请求计费未知。完整记录见[真实烟测报告](agent-query-model-live-smoke-20260907.md)。

阶段 5C 随后已实现 Macro-F1/硬槽位 F1、fallback 与用量对照，并在 48 条 development
数据上完成 20 次真实模型请求，见[对照报告](agent-understanding-comparison-20260907.md)。
这不改变上述 5 次烟测的历史统计，也不等于第三项代表性验收完成。下一步审核独立
holdout、冻结后对照并补 V2 同场景验证，再做逐 Node OpenTelemetry / Dashboard。
阶段 3B 在物流接口到达后接入；阶段 4 的 V2
发布、CI 和配置收口随后完成；最后按阶段 6 验证生产持久化、并发/回滚并固化求职材料。

## 6. 供应商依据

2026-09-07 核对 [DeepSeek Chat Completions 官方合同](https://api-docs.deepseek.com/api/create-chat-completion/)：
JSON object 模式、thinking 开关与 finish reason 来自该协议。本实现不声称供应商
强制遵循本项目的 JSON Schema。
