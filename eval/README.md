# SPB RAG 黑盒评估

`eval/` 是独立的轻量评估包，只通过 HTTP 调用 `rag-api`。它不导入在线或
离线应用、不直连 Milvus，也不读取本地爬取数据。

第一版关注：

- 可回答问题的 Recall@K、MRR@K 和 Gold 文档存活率；
- 双重相关性门槛的错误拒答率、错误回答率和 `finish_reason` 分布；
- 最终引用的 Gold 文档命中率与必需事实覆盖率；
- 检索/问答 P50、P95 延迟和 API 报告的生成 Token。

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
  "notes": ""
}
```

约定：

- `expected_outcome` 只能为 `answer` 或 `reject`；
- `answer` 样本必须提供至少一个 `gold_document_ids` 或
  `gold_source_urls`；
- `required_facts` 中每个子数组是一组同义表达，命中任意一个即算该事实命中；
- 文档级 `document_id` 优先于容易随切分策略变化的 chunk ID；
- 样例结构见 `datasets/template.jsonl`；
- 私有评估集放入 `datasets/private/`，该目录默认被 Git 忽略。

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
```

报告可能包含业务问题和模型回答，因此 `eval/reports/` 默认不会进入 Git。

## 指标口径

- Recall@K、MRR@K 的分母是所有带 Gold 来源的可回答样本；API 错误按未命中
  计入，同时单独报告错误数。
- `no_context`、`reranker_rejected`、`llm_rejected` 都视为拒答。
- 错误拒答率只在成功返回的可回答问答请求中计算。
- 错误回答率只在成功返回的无答案问答请求中计算。
- 引用 Gold 命中率只检查已生成答案的样本，错误拒答由门槛指标单独反映。
- 必需事实覆盖率是简单、可解释的字符串归一化匹配，不代表完整语义正确率。

第一版不包含 LLM-as-a-Judge、阈值扫描或运行结果对比，后续可在保持 HTTP
黑盒边界的前提下扩展。
