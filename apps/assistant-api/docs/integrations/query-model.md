# 意图理解模型接入

[Assistant API](../../README.md) · [项目文档导航](../../../../docs/README.md)

本页维护配置与失败边界；当前验收层级见[状态页](../../../../docs/current-status.md)，
历史烟测/同样本对照见[交付摘要](../../../../docs/history/agent-delivery-summary.md)。

## 职责与调用边界

Hybrid 先用显式入口、当前等待输入和规则决策，仅在需要语义补充时调用独立 DeepSeek Adapter。
模型只分类，不选择可执行工具/URL/权限，也不生成价格事实。Prompt 要求 slots=null；
Hybrid 对模型结果做 Pydantic 校验后，从原输入重新提取硬实体。
catalog_v2 的 schema/prompt profile 使用 product_price 替代 device_price，并重提设备/生鲜条件；
旧 profile 不接受新价格意图。配置模型不意味着可生成商品 ID、候选 token 或 SQL；
商品边界见[Understanding 专题](product-price-understanding.md)，历史 48 条对照不代表该新范围的真实模型效果。

| 模块 | 职责 |
| --- | --- |
| `adapters/deepseek_understanding.py` | 请求/响应 envelope、单次有界调用、合法 usage 观测 |
| `adapters/agent_http.py` | HTTP 连接池、TLS、大小/deadline 与稳定传输错误 |
| `query_model.py` | 显式开关、Model Port 和 Client 生命周期 |
| `services/query_understanding.py` | Prompt/规则/schema gate、硬实体重提和回退 |
| `agent_demo.py` / `configured_agent.py` | 在各自 lifespan 显式注入；开关不自动发布 V2 |
| `understanding_export.py` | 有预算的组件观测导出；不执行 Graph/Tool、不读取 Gold |

模型 Key 独立于 Assistant 服务 Key、RAG 服务 Key 和 RAG 模型配置，不隐式复用。
关闭开关不创建模型 Client；开启但缺配置时失败关闭。
启动/readiness 不发送付费探测，模型临时故障保留规则能力。

## 配置

仅合并必要字段到本地被忽略的 `apps/assistant-api/.env`，不覆盖原文件。
受控 `deployed_app` 不读 dotenv，须由其私有运行配置显式注入。
以下模型名是本项目当前配置，不是“最新/最优模型”推荐：

~~~dotenv
ASSISTANT_QUERY_MODEL_ENABLED=true
ASSISTANT_QUERY_MODEL_BASE_URL=https://api.deepseek.com
ASSISTANT_QUERY_MODEL_NAME=deepseek-v4-flash
ASSISTANT_QUERY_MODEL_API_KEY=<在本地填写你的 DeepSeek Key>
ASSISTANT_QUERY_MODEL_TIMEOUT_SECONDS=8
ASSISTANT_QUERY_MODEL_MAX_TOKENS=768
ASSISTANT_QUERY_MODEL_MAX_RESPONSE_BYTES=65536
ASSISTANT_QUERY_MODEL_MAX_CONCURRENCY=2
~~~

开启后 fallback 用户输入会发送给供应商并产生用量；必须明确授权、数据范围和次数。
真实 Key 不写聊天、命令参数、VITE_*、fixture 或日志。现有预算不自动授权后续复测。

本地演示与规则基线见[本地开发](../local-development.md)；真实模型实验使用不同的新 SQLite/新会话，
避免把已有幂等响应误当模型新调用。用脱敏 Trace 中 source=model 和 Prompt 版本确认识别来源，
query_model_call 日志本身只表示调用尝试。

## 请求与失败合同

- 固定 chat/completions、JSON object、非流式、thinking 关闭、有限输出，不提供 tools/function calling。
- 默认模型并发 2、总预算 8 秒、响应 65536 字节；总预算包含排队和网络。
  每次理解至多一次请求，无自动网络重试/模型修复回路。
- 拒绝非单 choice、非 assistant role、非 stop finish、空内容、tool calls、
  非法/重复键 JSON、非有限值或领域 schema 错误。JSON mode 不代替本地校验。
- 取消继续传播并释放容量；失败走 Hybrid 回退，不把失败后的 unknown 当模型成功识别。
- 日志只保留 provider/outcome/duration/固定失败码和合法 token 数；
  不记录问题、Prompt、完整响应、Key 或 reasoning_content。
- 模型传输熔断不代表语义准确率；模型预算与业务 Tool 的图重试预算独立。
- 响应成功但 checkpoint 未提交时仍有计费重复窗口，不承诺 exactly-once。
  超时未收到 usage 的调用成本未知，不能按零计。

供应商 JSON 模式/finish reason 依据曾核对的
[DeepSeek 合同](https://api-docs.deepseek.com/api/create-chat-completion/)；
协议改变时应复验 Adapter，不让线上自动猜兼容变体。

## 验证和质量验收

普通测试使用真实 Adapter + MockTransport，不需要 Key：

~~~bash
.venv/bin/pytest apps/assistant-api/tests/test_query_model_integration.py
~~~

覆盖规则免调用、模型语义/硬实体边界、异常结构、401/429/5xx、总 deadline、取消、并发、
lifespan、V2 补槽及重放。它验证工程合同，不给出泛化准确率。

组件流程为 Eval 导出无 Gold 输入 → Assistant observations → Eval 重算/比较。
`rules` 导出不构造模型；`hybrid --allow-live-model --max-model-calls N` 必须另获授权。
审核冻结、指标分母、失败/预算跳过与用量口径统一见 [Eval 指南](../../../../eval/README.md)。
独立 holdout 与对应 V2 多轮验收仍在[路线图](../../../../docs/roadmap.md)，不再重列阶段进度。
