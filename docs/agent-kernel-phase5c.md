# Phase 5C：Understanding 质量评测与有界同样本对照

> 日期：2026-09-07；`assistant-api 0.3.5`、`eval 0.6.0`。
> 组件评测工程、离线基线与 20 次授权请求的真实对照已完成。48 条数据均为 synthetic
> development / draft，不是人工审核过的代表性 holdout，不能替代完整 V2 验收。
> 完整数值及指纹见[真实对照证据](agent-understanding-comparison-20260907.md)。

## 1. 本阶段内容与模块边界

| 模块 | 实现与责任 |
| --- | --- |
| `eval/understanding_schema.py` | 独立的标注、观测和 manifest 契约，不导入 Assistant |
| `eval/understanding_dataset.py` | 数据划分防泄漏、去重、冻结指纹、无 Gold 请求及观测校验 |
| `assistant-api/understanding_export.py` | 调用现有 Understanding Port，串行／有界导出；不执行 Graph 或 Tool |
| `query_model.py` / DeepSeek Adapter | 复用真实供应商装配、唯一 Client 所有权、可装饰 Port 和脱敏 call observer |
| `eval/understanding_metrics.py` | Intent／硬槽位 F1、缺槽、多意图／控制、失败、用量、延迟与门禁 |
| `eval/understanding_reporting.py` | 从原始观测重算同 Gold 对照、差异、JSON／Markdown 与 review queue |

执行链为：标注数据 → Eval 导出不含 Gold 的 inputs → Assistant 导出 observations →
Eval 独立评分／比较。既有 `agent-run` / `agent-compare` 继续只走 V2 HTTP。
选择原因见 [ADR-0010](adr/0010-understanding-component-evaluation.md)。

组件结果不等于最终 Workflow 状态：例如 active postage + expected weight 输入
“500克”，本次理解只能提取重量；因为没有传历史槽位，组件结果仍缺 origin/destination。
跨轮合并、冲突确认、路由、取消与恢复的完整正确性继续由 V2 多轮评测验证。

## 2. 标注与数据冻结规范

数据集：`eval/datasets/query-understanding-development-v1.jsonl`，48 条，覆盖六类意图、
口语表达、邮件号、地区、重量、缺槽、显式入口、给定会话上下文、多意图、地区／重量
歧义、当前轮更正、控制和范围外输入。四条已见真实烟测输入明确打 `seen_smoke` 标签。
旧 `agent-understanding-v1.jsonl` 保留历史含义，不把其中旧 holdout 标签当作未见证据。

新契约 `qu-case-v1` 关键字段：

- `id/group_id/category/tags`：使用无 PII 的稳定标识；同语义模板、释义变体、同场景的
  多轮切片归入同 group，不能跨 development/holdout。规范化后重复输入直接拒绝。
- `split/provenance/annotation_status`：默认 development/synthetic/draft；holdout 要求
  reviewed 且非 seen_smoke。校验只能检查声明，不能替代真实人工审核或近重复复核。
- `input`：message、可选 active_intent/explicit_intent、当前等待的 expected_slots。
  这是被测组件的真实输入条件，不从 `gold` 自动回填上下文。
- `gold.intent`：单目标明确标注；多意图／歧义设 null、列出候选集合、关闭不稳定的
  单目标 slot 评分。控制命令单独标 control，不与业务意图 F1 混算。
- `gold.slot_values`：完整列出本轮四种受测硬字段；不是只标一部分的稀疏断言。邮件号
  大写、地区用规范名、重量统一公斤且消除无意义尾零。冲突／未消歧实体不当作已填。
- `gold.missing_slots`：组件层当前缺槽；null 明确表示本行不评分，而不是空列表。

下一份 holdout 应另行收集并人工标注，在运行前冻结数据、Prompt、规则和地区字典；
冻结后保留失败，不能根据其错误修改规则再沿用“未见 holdout”名称。

## 3. 指标口径

| 指标 | 口径 |
| --- | --- |
| Intent Accuracy / Macro-F1 | 五业务 + unknown 固定六标签；无支持类别 F1=0 并标覆盖缺口，控制／多目标行排除并报告数量 |
| Joint hard-slot micro-F1 | 四个原子槽位的 TP/FP/FN；错值 = FP + FN，额外字段 = FP，缺值 = FN；错误意图导致漏抽也计入 |
| Slot exact match | 所有已标槽位集合全等；缺失／错误 observation 不能因两边为空而通过 |
| Missing-slot accuracy | 单独比较缺槽集合；不充当实体 Slot F1 |
| Multi/control accuracy | 独立统计，候选集合不完整或多余也导致相应样本失败 |
| Model call/failure rate | 调用率分母为完整样本数；失败率分母为实际尝试数；未调用时失败率为 N/A |
| unknown 分类 | 正常模型 unknown 与失败后规则 unknown 分开；后者即便标签正确，仍计模型失败与 review queue |
| 用量 | 合法 usage 的已知 token 累计 + 用量未知调用数 + 未观测行；不宣称账单总成本 |
| 延迟 | 已观测组件／模型调用的样本数及插值 P50/P95；包含超时，不给缺失行补零；不是 HTTP／Node span 延迟 |

数据、无 Gold 输入、实现、非敏感配置和 Prompt 各自有 SHA256／版本；对照直接验证
两份原始观测与同一数据/输入匹配后重算。缺失行和预算 skipped 留在失败分母，未知或
重复 observation、损坏 JSON 和错误指纹直接拒绝，不静默丢弃。报告不复制问题正文、
Gold 槽位值、Prompt 或 Key；slot fingerprint 仍只是可关联假名化，私有报告不得公开。

初始可调门禁：六类有支持、所有 observation 为 ok、Macro-F1 ≥ 0.90、硬槽位 F1 ≥
0.95、模型失败率 ≤ 0.05（规则运行未调用模型时此项免检）。这是工程初值，不是生产
SLA；完整 case pass、切片和 review queue 仍需审核，不能只看门禁灯。

## 4. 运行方法

所有运行文件放入 Git 忽略的 `eval/reports/`；请求和 observation 文件拒绝覆盖，复测
请使用新的输出路径。新 entry point 需要先执行 workspace 同步，例如 `uv sync --all-packages`。

```bash
uv run --package spb-eval spb-eval understanding-prepare \
  --dataset eval/datasets/query-understanding-development-v1.jsonl \
  --split development --output eval/reports/qu-run/requests.json

uv run --package spb-assistant-api spb-assistant-understanding-export \
  --requests eval/reports/qu-run/requests.json \
  --output eval/reports/qu-run/rules.jsonl --mode rules

uv run --package spb-eval spb-eval understanding-score \
  --dataset eval/datasets/query-understanding-development-v1.jsonl \
  --observations eval/reports/qu-run/rules.jsonl \
  --output-dir eval/reports/qu-run/scored --fail-on-gate
```

`rules` 模式不构造 Settings、不读取模型凭据。当前基线未过 Macro-F1 门禁，最后一条
命令在报告落盘后返回 3，这是预期暴露的覆盖缺口。

真实对照必须单独获得授权，并先按[模型配置说明](agent-query-model-integration.md)配置
本地 Key。下列命令最多 20 次模型尝试，串行、每次总预算 8 秒，不自动重试；不在
默认测试／CI 中运行。

```bash
ASSISTANT_QUERY_MODEL_TIMEOUT_SECONDS=8 \
uv run --package spb-assistant-api spb-assistant-understanding-export \
  --requests eval/reports/qu-run/requests.json \
  --output eval/reports/qu-run/hybrid.jsonl --mode hybrid \
  --allow-live-model --max-model-calls 20

uv run --package spb-eval spb-eval understanding-compare \
  --dataset eval/datasets/query-understanding-development-v1.jsonl \
  --baseline eval/reports/qu-run/rules.jsonl \
  --experiment eval/reports/qu-run/hybrid.jsonl \
  --output-dir eval/reports/qu-run/comparison --fail-on-gate
```

预算按 Model Port 实际尝试扣减；耗尽后该类样本记 skipped，规则免模型的样本仍继续。
退出码：0 完成，2 配置／契约／文件错误，3 有错误／跳过或启用的质量门禁未过。已完成
observation 按行 flush，正常取消保留可评分前缀；中断后的缺失行不是零成本。供应商已
执行但本地尚未保存时仍可能产生未知账单，不承诺 exactly-once。

## 5. 本地证据与后续顺序

离线验证：新增 38 个测试 case，涵盖手工 F1 分母、错值／多抽／漏抽、缺失行、模型
unknown 与失败回退、用量缺失、文件契约、数据泄漏防护、预算、取消、Gold 隔离和
真实生产 Adapter 的 Mock 对照。联合回归 `71 passed`，完整 Python `380 passed`。
Web 与公开 V2 schema 未改动。本地 Rules 基线：48 条中 34 条完整通过，Macro-F1
0.7068、硬槽位 F1 0.9600、缺槽提示正确率 0.8478，20 条具备 fallback 条件。

随后经授权调用真实模型恰好 20 次，全部成功且无重试，另外 28 条免模型。Hybrid
完整通过 48/48，Macro-F1／硬槽位 F1 均 1.0000，14 条改善、0 条退化；已知 total
tokens 40,097，模型 P95 999.20 ms。收益与延迟代价仅适用于本次 development 组件
运行；六条正常模型 unknown 与模型失败回退分别记账，不因全通过而声称代表性验收。

阶段 5 的下一验收重点仍是：审核独立 holdout → 冻结后真实同样本对照 → V2 同场景
验证。随后实现逐 Node OpenTelemetry 与 Dashboard；阶段 3B 继续等待真实物流合同，
阶段 4 正式发布和阶段 6 生产持久化／多副本验收不因组件 F1 存在而完成。

后续进度：[Phase 5D](agent-kernel-phase5d.md) 已补人工审核冻结工具及 V2 同场景 Mock
回归，不是独立真实 holdout 验收。等待审核数据期间，可继续可观测性切片。
