# Understanding Rules / Hybrid 对照：2026-09-07

> Phase 5C；`assistant-api 0.3.5`、`eval 0.6.0`；当前 HEAD `27f735a` 之上的未提交工作树。
> 这是 48 条 synthetic development / draft 数据的组件实验，不是独立 holdout、完整
> Workflow 验收或生产质量报告。用户本轮授权上限 20 次模型请求，实际尝试恰为 20 次。

## 1. 实验条件

- 数据：`eval/datasets/query-understanding-development-v1.jsonl`，48 条；六类意图、
  口语表达、硬实体、缺槽、给定上下文、控制、多意图、歧义和当前轮更正。4 条已有
  `seen_smoke` 标签；所有标签仍为 draft，不能通过改 split 变成未见 holdout。
- 先冻结标注及无 Gold 输入，完成离线测试，再在相同输入上运行 Rules 与 Hybrid；
  两次实现、规则、地区字典和 Prompt 哈希一致，期间未针对错误修改规则／Prompt。
- Rules 不构造模型、不读取凭据；Hybrid 仅对规则需要 fallback 的输入调用 DeepSeek。
  实际模型配置为 `deepseek-v4-flash`、Prompt `query-understanding-v2`、max_tokens 768。
- 串行执行，每次模型总 deadline 8 秒，无自动重试；28 条免模型、20 条实际调用。
  无真实业务数据，不调用物流、RAG 或价格工具，不推进 Graph／历史状态。
- 两次原始观测与同一完整 Gold 重新评分，指标版本 `qu-metrics-v1`；不是比较摘要数字，
  也不是同一份 observation 的自对照。完整离线回归为 `380 passed`。

## 2. 结果及分母

| 指标 | Rules | Hybrid |
| --- | ---: | ---: |
| 完整样本通过 | 34/48 | 48/48 |
| Intent Macro-F1，固定六类 | 0.7068 | 1.0000 |
| Intent Accuracy | 30/44 | 44/44 |
| 联合硬槽位 micro-F1 | 0.9600 | 1.0000 |
| 硬槽位 TP / FP / FN | 24 / 0 / 2 | 26 / 0 / 0 |
| 缺槽集合正确 | 39/46 | 46/46 |
| 实际模型调用 / 全部样本 | 0/48 | 20/48（41.67%） |
| 模型失败 / 实际尝试 | N/A | 0/20 |
| 预算跳过 / 未观测行 | 0 / 0 | 0 / 0 |
| 已知 total tokens / 用量未知调用 | 0 / 0 | 40,097 / 0 |
| 模型调用 P50 / P95 | N/A | 725.34 / 999.20 ms |
| 组件 P95，48 条混合路径 | 0.08 ms | 933.03 ms |

Intent 评分排除 2 条控制和 2 条多意图，因此分母为 44；不是用 48 作六分类准确率分母。
槽位评分含 46 行、26 个 Gold 原子值，评测邮件号、起寄地、寄达地和公斤重量；没有
Gold 实体的负例参与误抽统计，不会凭空增加 TP。不覆盖设备规格抽取或完整行政区库。

14 条改善、0 条退化、34 条完整通过状态不变。改善样本为 `policy-04/05/06`、
`device-04/05/06`、`tracking-04/05/06`、`delivery-04/05/06`、`postage-04/06`。
主要变化是口语意图从规则 unknown 转为正确业务意图；`delivery-05` 的正确意图又允许
确定性解析器重提两个地区，贡献槽位 FN 的减少。不能解释成“模型直接生成可信硬字段”。

20 次模型成功中，14 次改善业务意图、6 次正常返回 unknown；失败回退 unknown 为 0。
Provider 返回的合法 usage 合计 prompt 38,786、completion 1,311、total 40,097 tokens。
这些是已知用量，不包含价格、缓存折扣或账单校验结论；不得当作精确货币成本。

Rules 未通过 Macro-F1 ≥ 0.90 的工程门禁；Hybrid 通过全部组件门禁且无逐样本退化。
同时 Hybrid 组件 P95 明显高于纯规则，质量改善并非无成本。上述延迟来自一次本地串行
组件运行，模型样本只有 20 条，不是 V2 端到端、Node span、并发压测或生产 SLA。

## 3. 冻结指纹与本地产物

| 对象 | SHA256 |
| --- | --- |
| 数据集原始文件 | `598868598a5f9d3ab872e0e36ba02c9daa682ee3fb3de9d4a8103030308d47eb` |
| 无 Gold 请求规范化指纹 | `a95fe6e8c79bab3cf71195812d8faf9366d2961b5f1c5bb58b13e75f5c4a9b88` |
| 两组共同的组件实现 | `76ad8c56419f6467fc3360e3b52cd9a4badc6956b1008cdc41ed666fc8a759bf` |
| 两组共同的 Prompt／schema | `fd9b96fe4a9e0551227077ff8278e0aeae6e12d34e3bedfae0d42cf4bc3f5eb1` |
| Rules 非敏感配置 | `f35461029098f47a9bc5b9b261519668ad8250ff94c7dcc85d806d76be43e172` |
| Hybrid 非敏感配置 | `19f0049f330c3cd3666ef73ee03a3a97953e0bfae1adba73b7f55f8454029b82` |
| Rules 原始 observation | `355c78342a2578ea89cef158743be3e67e0a25ee817dce0e786ab858f7f08656` |
| Hybrid 原始 observation | `3b741d255d6670e40f146495aa8d55bfc308cb7d83d3e9527507f15be230dac7` |
| 对照 report.json | `fa69d9bd94a3d88c7dd0dfe3ddabec4bf90449a14b380f1fd1a82871b6e4ec5c` |

实现指纹覆盖导出器列出的模型／解析／配置相关源文件，不是整个 Git 工作树哈希，也不
包含文档。配置指纹不含 Key；观测不含 query、槽位原值、Prompt 或 Provider 正文。
槽位指纹只是可关联假名化，运行产物仍应按私有数据管理。

本地完整产物位于 Git 忽略的 `eval/reports/understanding-phase5c/`：

- 输入：`requests.json`。
- 最终原始观测：`rules-final.jsonl`、`hybrid-live.jsonl`。
- Rules 报告：`final-baseline/20260907-041516-36590262/`。
- 同样本对照：`comparison/20260907-041516-03d68783/`，含 JSON、Markdown 和 review queue。

本文件为可提交精简证据；上述原始运行文件不会随 Git clone 分发。早期 `rules.jsonl`
是工程开发中的中间产物，不用于本次最终对照。

无需再次付费即可重新计算这份报告（输出目录每次生成唯一子目录）：

```bash
.venv/bin/spb-eval understanding-compare \
  --dataset eval/datasets/query-understanding-development-v1.jsonl \
  --baseline eval/reports/understanding-phase5c/rules-final.jsonl \
  --experiment eval/reports/understanding-phase5c/hybrid-live.jsonl \
  --output-dir eval/reports/understanding-phase5c/recomputed --fail-on-gate
```

实际模型运行使用下列参数；它是审计记录，不代表后续复测仍有授权：

```bash
ASSISTANT_QUERY_MODEL_TIMEOUT_SECONDS=8 \
.venv/bin/python -m spb_assistant_api.understanding_export \
  --requests eval/reports/understanding-phase5c/requests.json \
  --output eval/reports/understanding-phase5c/hybrid-live.jsonl \
  --mode hybrid --allow-live-model --max-model-calls 20
```

## 4. 结论与下一步

本次证明了“规则优先 + 受限模型 fallback”对这组已知合成口语样本有增益，且预算、
独立评分和证据链可运行。48 条全通过不意味着泛化完成；样本由开发过程构造、标签
未人工复核、部分已见，且同族措辞和单次模型波动没有经过独立验证。

下一步：另收集并人工审核 holdout／困难负例，按语义 group 隔离并冻结；获得新的模型
调用预算后进行一次性对照，并在 V2 上验证对应补槽／多轮／恢复场景。之后再补逐 Node
OpenTelemetry 与 Dashboard。真实物流合同、正式 V2 发布和多副本硬化仍独立待验。

先前[模型烟测](agent-query-model-live-smoke-20260907.md)的 5 次请求（含一次超时）属于
另一批实验，必须保留，不并入本次 20 次的成功率。设计与运行说明见
[Phase 5C](agent-kernel-phase5c.md)，面试素材见[技术复盘](project-retrospective.md)。
