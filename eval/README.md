# SPB RAG、Assistant、Agent 黑盒与 Understanding 组件评估

`eval/` 是独立的轻量评估包（当前 `0.7.0`），端到端评测只通过 HTTP 调用 `rag-api`
或 `assistant-api`；Understanding 组件另通过版本化输入／观测文件测量。它不导入
在线或离线应用、不直连 Milvus/MySQL，也不读取本地爬取数据或 Graph checkpoint。

当前评估能力包括：

- 可回答问题的 Recall@K、MRR@K 和 Gold 文档存活率；
- 双重相关性门槛的错误拒答率、错误回答率和 `finish_reason` 分布；
- 最终引用的 Gold 文档命中率与必需事实覆盖率；
- 检索/问答 P50、P95 延迟和 API 报告的生成 Token；
- 基于 shadow-mode 检索结果离线扫描 reranker threshold；
- 按错误回答、错误拒答和 Gold 存活约束推荐候选阈值；
- 对同一数据集的 baseline 与 experiment 报告做指标及逐样本对比；
- 聚合重复运行，统计质量指标极差、路由/引用/答案文字一致率；
- 为每次运行自动生成 `review-queue.md` 人工复核队列。
- 对 Assistant 校验固定模式分发、结束状态、拒答原因、信息缺口、证据类型和字段
  完整性；
- 评估设备价格候选 Product/SKU Recall，并识别拒答结果泄露证据的问题；
- 记录统一助手在指定并发度下的延迟、吞吐、错误和 Token 基线。
- 通过 `/v2/agent/messages` 顺序推进同一 conversation 的多轮黑盒场景；
- 计算 Intent Accuracy、Required Input Accuracy、Wrong Tool Rate、Task Completion、
  Recovery、API Error Rate 和 Turn P50/P95，并生成可供 CI 使用的质量门禁。
- 严格验证 dataset hash、Gold 标签和门禁阈值后，对比 Agent baseline/experiment 的
  核心指标和逐 Turn 回归。
- 独立计算 Understanding 六类 Intent Macro-F1、联合硬槽位 micro-F1、缺槽提示、
  正常 unknown／失败回退、调用预算、已知／未知用量与组件延迟，并从原始观测重算对照。

## 资费公开 Workflow 回归（Phase 3B-P / P3）

资费 P3 另有 [13 场景 / 28 Turn development 数据集](datasets/agent-postage-workflow-development-v1.jsonl)，
仅经公开 V2 评测。新增 expected_quote_basis_values 用于断言 result.quote_basis 的点分字段
（例如 source.source_type / fees.0.included_in_amount），纳入 Turn / Case 通过判定；
expected_result_values 继续只表示 result.data，不改变旧数据集语义。
独立 Eval 镜像不导入服务端；合成协议响应由应用侧 fixture 提供，不能读取 Gold 生成答案。
复现入口、8 次 Mock 请求证据及限制见 [P3 说明](../apps/assistant-api/docs/integrations/postage.md)。
全部通过不是模型质量或真实费率正确率。

## Understanding 组件评测（Phase 5C）

它与下文 RAG／Assistant／Agent HTTP 数据集和命令分开，不用 `required_inputs`
正确率代替 Slot F1。流程为 `understanding-prepare` → Assistant 本地 exporter →
`understanding-score` / `understanding-compare`；只有模型导出阶段可能访问付费模型。

```bash
uv run --package spb-eval spb-eval understanding-prepare \
  --dataset eval/datasets/query-understanding-development-v1.jsonl \
  --output eval/reports/qu-example/requests.json

uv run --package spb-assistant-api spb-assistant-understanding-export \
  --requests eval/reports/qu-example/requests.json \
  --output eval/reports/qu-example/rules.jsonl --mode rules

uv run --package spb-eval spb-eval understanding-score \
  --dataset eval/datasets/query-understanding-development-v1.jsonl \
  --observations eval/reports/qu-example/rules.jsonl \
  --output-dir eval/reports/qu-example/scored --fail-on-gate
```

当前规则基线 Macro-F1 未达门禁，最后一条命令会先保存报告，再返回 3。请求与原始观测
拒绝覆盖；新 entry point 需先 `uv sync --all-packages`。普通测试、评分与规则导出不
需要模型 Key；真实导出须显式开启、限制调用预算并另获授权，不放入默认 CI。

新数据契约使用 `development/holdout`；holdout 必须 reviewed、不能含 seen_smoke，且
同 group 不跨 split。该校验不能替代真实人工标注和近重复审核。默认提供的 48 条是
synthetic development / draft；真实对照已用 20 次模型请求完成，不代表泛化准确率。
报告不含问题正文或实体原值，但 fingerprint 可关联，私有数据与报告仍不得公开。

模型配置和调用预算见 [Assistant Query Model](../apps/assistant-api/docs/integrations/query-model.md)；
已复核的历史结果见
[development 对照证据](../docs/history/agent-delivery-summary.md)。

## 人工审核冻结与 V2 对齐（Phase 5D）

`understanding-review --dataset <候选 JSONL> --against <已见 JSONL> --output-dir <新目录>`
生成污染 audit、全部 pending 的 review.json 与审核说明；`--against` 可重复。候选和参考
均使用 `qu-case-v1`，候选在批准前保留 development。审核者人工核对完整 Gold、语义近
重复／group，填写审核别名、时间、检查项和逐条 approve/exclude 后，使用
`understanding-freeze` 的同组参数并加 `--review <review.json>` 冻结数据与无 Gold 请求。
源数据／参考集／逐条 SHA 变化、pending、已见污染或不完整审核会阻止冻结。

这些命令不推理、不读取 Key、不代签审核，也不自动冻结代码／Prompt／配置。产物放入
`eval/datasets/private/` 或 `eval/reports/`，目标目录拒绝覆盖。各命令的完整参数可用 `spb-eval <命令> --help` 查看。

V2 新夹具 `datasets/agent-understanding-workflow-development-v1.jsonl` 含 13 场景／28
Turn，支持新 `development` split，不改变旧校准／holdout 数据。受控 Mock 模型的报告可
用 `.venv/bin/python apps/assistant-api/tests/test_phase5d_understanding_v2.py --output-dir
eval/reports/understanding-phase5d/v2-fixture` 生成；该脚本固定使用 ASGI／Mock transport，
不允许切换到付费模型。它验证真实 Workflow 的集成语义，不衡量模型质量。

## 数据集

数据集使用 JSONL，每行一个样本：

```json
{
  "id": "licence-001",
  "category": "direct_answer",
  "question": "问题文本",
  "expected_outcome": "answer",
  "gold_document_ids": ["稳定的 document_id"],
  "gold_source_urls": [],
  "required_facts": [
    ["事实表达 A", "事实 A 的同义表达"],
    ["事实表达 B"]
  ],
  "filters": {},
  "difficulty": "medium",
  "split": "calibration",
  "source_type": "html",
  "tags": ["licence", "numeric"],
  "notes": ""
}
```

约定：

- `expected_outcome` 只能为 `answer` 或 `reject`；
- `answer` 样本必须提供至少一个 `gold_document_ids` 或
  `gold_source_urls`；
- `required_facts` 中每个子数组是一组同义表达，命中任意一个即算该事实命中；
- `difficulty` 为 `easy`、`medium` 或 `hard`，默认 `medium`；
- `split` 为 `calibration` 或 `holdout`，默认 `calibration`；
- `source_type` 和 `tags` 用于报告切片，不改变在线请求；
- 文档级 `document_id` 优先于容易随切分策略变化的 chunk ID；
- 样例结构见 `datasets/template.jsonl`；
- 私有评估集放入 `datasets/private/`，该目录默认被 Git 忽略。

冻结完整数据集后，可以按 `split` 字段导出校准集、留出集和带 SHA256 的
manifest。目标文件已存在时命令会拒绝覆盖：

```bash
uv run --package spb-eval spb-eval split-dataset \
  --dataset eval/datasets/private/core-v1/full.jsonl \
  --output-dir eval/datasets/private/core-v1
```

## 运行

API Key 只通过环境变量传入，不写入配置或报告：

```bash
export EVAL_BASE_URL=http://127.0.0.1:8080
export EVAL_API_KEY=your-rag-api-key

uv run --package spb-eval spb-eval run \
  --dataset eval/datasets/private/core.jsonl \
  --label full-chain-050 \
  --mode all \
  --top-k 5 \
  --candidate-k 40 \
  --concurrency 5
```

模式：

| 模式 | 调用 |
|---|---|
| `retrieve` | 只调用 `/v1/retrieve` |
| `chat` | 只调用非流式 `/v1/chat` |
| `all` | 对每个样本依次执行检索和问答 |

报告写入 `eval/reports/<时间>-<label>/`：

```text
run.json       完整运行配置、汇总和逐样本结果
cases.jsonl    便于脚本继续分析的逐样本结果
summary.md     面向人工阅读的核心指标与错误路由
review-queue.md 自动筛出的检索、门槛、引用和事实覆盖问题
```

报告可能包含业务问题和模型回答，因此 `eval/reports/` 默认不会进入 Git。

如果人工复核只修订了 Gold 来源、同义事实或切片标签，且问题文本没有变化，
可直接复用保存的在线响应离线重算，避免再次消耗模型调用：

```bash
uv run --package spb-eval spb-eval recalculate \
  --report eval/reports/<run>/run.json \
  --dataset eval/datasets/private/core-v1/full.jsonl \
  --label core-v1-reviewed
```

只要样本 ID 集合不同或问题文本发生变化，命令就会拒绝复用。

重复运行后可生成稳定性报告：

```bash
uv run --package spb-eval spb-eval stability \
  --report eval/reports/<run-1>/run.json \
  --report eval/reports/<run-2>/run.json \
  --report eval/reports/<run-3>/run.json
```

新运行还会在 `summary.efficiency` 中记录端到端墙钟耗时和实际请求吞吐。

## Assistant 评估

Assistant 数据集与政策 RAG 专项集分开。每行只描述一次独立的单轮请求，不包含
会话历史：

```json
{
  "id": "price-001",
  "category": "price_candidate",
  "mode": "device_price",
  "question": "设备品牌、型号和规格",
  "expected_outcome": "answer",
  "expected_finish_reasons": ["stop"],
  "expected_product_ids": ["gold-product-id"],
  "expected_sku_ids": [],
  "min_evidence_count": 1,
  "tags": ["exact-model"]
}
```

`expected_outcome` 取 `answer`、`no_match` 或 `need_more_info`。可通过
`expected_reason_codes` 精确约束拒答原因，通过 `expected_missing_fields` 校验
补充信息提示；价格样本可以提供 Product/SKU Gold，政策样本不能填写价格 Gold。
完整字段示例见 `datasets/assistant-template.jsonl`。

Agent V2 的 Query Understanding 回归夹具位于
`datasets/agent-understanding-v1.jsonl`；它面向内部 Understanding schema，不由黑盒
Runner 直接读取。Phase 5A 新增的公开 Workflow 数据集位于
`datasets/agent-workflow-v1.jsonl`，只断言 V2 API 能观察到的停止态、意图、下一动作、
required inputs、结果状态和结果字段。

运行统一助手评估：

```bash
export EVAL_ASSISTANT_BASE_URL=http://127.0.0.1:8081
export EVAL_ASSISTANT_API_KEY=your-assistant-service-key

uv run --package spb-eval spb-eval assistant-run \
  --dataset eval/datasets/private/assistant-core.jsonl \
  --label assistant-full-chain \
  --concurrency 5
```

该命令固定调用非流式 `/v1/chat`，请求只包含 `mode`、`question` 和
`stream=false`。报告目录结构与 RAG 专项运行相同，核心结果包括：

- 样本整体通过率和 `policy` / `device_price` 分模式通过率；
- 模式分发、`finish_reason`、`reason_code`、缺失字段和最少证据数量通过率；
- 证据类型正确率、政策/价格必要字段完整率和拒答证据泄露率；
- 价格候选 Product/SKU Recall；
- API 错误、P50/P95 延迟、墙钟吞吐和生成 Token。

模板问题只用于说明格式，不能作为评估结论。真实问题、数据库标识、回答和报告
必须放在 Git 忽略的 `datasets/private/` 与 `reports/` 下。

## Agent V2 多轮评估

每个场景拥有一个或多个顺序执行的 `turns`。Runner 在场景之间按 `--concurrency`
并发，在场景内部复用服务端返回的 `conversation_id`，因此可以真实覆盖 LangGraph
`interrupt/resume`，而不会把历史消息拼进请求冒充状态恢复。

```json
{
  "id": "tracking-fill-001",
  "category": "multi_turn_slot_fill",
  "turns": [
    {
      "message": "帮我查一下邮件",
      "expected_phase": "waiting_user",
      "expected_intent": "tracking",
      "expected_next_action": "collect_slots",
      "expected_required_inputs": ["mail_no"]
    },
    {
      "message": "1234567890123",
      "expected_phase": "completed",
      "expected_intent": "tracking",
      "expected_next_action": "complete",
      "expected_result_status": "success",
      "expected_result_values": {
        "mail_no": "1234567890123"
      }
    }
  ]
}
```

本地五能力 Demo 不需要 API Key：

```bash
ASSISTANT_QUERY_MODEL_ENABLED=false ASSISTANT_HOST=127.0.0.1 \
ASSISTANT_AGENT_DEMO_DB=/tmp/spb-agent-eval.db \
uv run --package spb-assistant-api spb-assistant-agent-demo

uv run --package spb-eval spb-eval agent-run \
  --dataset eval/datasets/agent-workflow-v1.jsonl \
  --label phase5a-local-fixture \
  --base-url http://127.0.0.1:8081 \
  --concurrency 4 \
  --fail-on-gate
```

评测真实环境时，使用 `EVAL_AGENT_BASE_URL` 和 `EVAL_AGENT_API_KEY`；密钥只进入请求头，
不写入配置、逐样本结果或报告。`EVAL_AGENT_API_KEY` 未设置时会兼容读取已有的
`EVAL_ASSISTANT_API_KEY`。

默认质量门禁为：Golden 场景通过率等于 1、Intent 与 Required Input Accuracy 不低于
0.95、Wrong Tool Rate 等于 0、Task Completion 与 Recovery 不低于 0.90、API Error
Rate 等于 0。
`--fail-on-gate` 会在报告写完后用退出码 3 表示门禁失败。每次运行生成：

```text
run.json          完整配置、服务能力快照、汇总和逐场景结果
cases.jsonl       逐场景机器可读结果
summary.md        指标、分意图结果和门禁表
review-queue.md   自动归因到理解/路由/工具/基础设施的复核队列
quality-gate.json CI 可直接读取的门禁结果
```

Phase 5A 的 13 场景 / 17 Turn 本地 fixture 基线只证明评测链路和确定性规则回归，不能
当作代表性生产准确率。真实接口、Structured Model 和更大 holdout 数据集接入后必须
生成独立报告，不能沿用该 Demo 数字。

### Agent Baseline / Experiment 对比

两份 `agent-run` 报告必须来自同一冻结数据集，并使用相同质量门禁：

```bash
uv run --package spb-eval spb-eval agent-compare \
  --baseline eval/reports/<baseline>/run.json \
  --experiment eval/reports/<experiment>/run.json
```

工具要求两份报告都有且共享相同 `dataset_sha256`，并逐场景校验完整 Gold Turn、样本
ID 和门禁阈值。任一条件不一致都会拒绝对比。输出包含场景/Turn、Intent、Required
Input、非必要澄清、Wrong Tool、Task Completion、Recovery、API Error 与 Turn P95
差值，并列出逐 Turn improvement/regression。API error 与缺失 Turn 都按失败处理，不会
因没有成功响应而从比较中消失；两侧指标从逐 Turn observation 重新计算，不直接信任
保存的汇总字段。

## 指标口径

- Recall@K、MRR@K 的分母是所有带 Gold 来源的可回答样本；API 错误按未命中
  计入，同时单独报告错误数。
- `no_context`、`reranker_rejected`、`llm_rejected` 都视为拒答。
- 错误拒答率只在成功返回的可回答问答请求中计算。
- 错误回答率只在成功返回的无答案问答请求中计算。
- 引用 Gold 命中率只检查已生成答案的样本，错误拒答由门槛指标单独反映。
- 必需事实覆盖率是简单、可解释的字符串归一化匹配，不代表完整语义正确率。
- Recall、错误拒答率、错误回答率和引用命中率同时报告 Wilson 95% 置信区间；
- `summary` 还按 `category`、`difficulty`、`source_type` 和 `split`
  保存切片指标，
  `summary.md` 默认展示分类切片。

## Reranker 阈值扫描

阈值扫描必须使用只执行检索的 shadow-mode 实验。shadow mode 会计算并返回
rerank 分数，但不会在服务端按当前阈值删除候选：

```bash
RAG_RERANK_ENABLED=true \
RAG_RERANK_SHADOW_MODE=true \
uv run --package spb-rag-api spb-rag-api

uv run --package spb-eval spb-eval run \
  --dataset eval/datasets/private/core.jsonl \
  --mode retrieve \
  --top-k 20 \
  --label rerank-shadow
```

对生成的 `run.json` 扫描阈值：

```bash
uv run --package spb-eval spb-eval threshold-scan \
  --report eval/reports/<run>/run.json \
  --start 0.10 \
  --stop 0.90 \
  --step 0.05 \
  --max-false-accept-rate 0.10 \
  --max-false-reject-rate 0.15 \
  --min-gold-survival-rate 0.80
```

也可以重复使用 `--threshold 0.3 --threshold 0.5` 指定离散候选。输出包括：

- answer 样本中所有候选均低于阈值的错误拒答率；
- reject 样本中至少一个候选高于阈值的错误回答风险；
- Gold 来源经过该阈值后仍保留的比例；
- 满足全部约束时的最高安全阈值；
- 没有候选满足约束时，总违约量最小但明确标记“未通过”的参考值。

只要本次 shadow run 存在检索 API 错误或非空候选缺少 `rerank_score`，工具就
不会给出可直接采用的阈值，避免从不完整样本中产生误导性推荐。

不要使用 shadow-mode 的 `/v1/chat` 结果评估完整双门槛链路，因为此时第一道
本地拒答已被旁路。

## Baseline / Experiment 对比

两份报告必须使用完全相同的样本 ID、Gold 标签和 `top_k`：

```bash
uv run --package spb-eval spb-eval compare \
  --baseline eval/reports/<baseline>/run.json \
  --experiment eval/reports/<experiment>/run.json
```

对比报告包含：

- Recall、MRR、误拒、误放、引用、事实覆盖、延迟、Token 和 API 错误差异；
- 指标的改善、退化、持平或不可比标记；
- 逐样本 gate、retrieval 和 citation 回归/改善列表。

当前评估不使用 LLM-as-a-Judge 作为唯一结论。人工质量判断通过每次运行自动生成的
`review-queue.md` 完成，避免让 DeepSeek 自评成为唯一结论。
