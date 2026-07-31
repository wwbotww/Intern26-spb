# SPB RAG 黑盒评估

`eval/` 是独立的轻量评估包，只通过 HTTP 调用 `rag-api`。它不导入在线或
离线应用、不直连 Milvus，也不读取本地爬取数据。

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
