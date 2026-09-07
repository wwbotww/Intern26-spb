# Phase 5D：人工审核冻结与 Understanding → V2 工作流回归

> 日期：2026-09-07；`eval 0.7.0`，Assistant Runtime 保持 `0.3.5`。
> 离线工程切片已完成，本轮没有付费模型请求，也没有生成“已人工审核”的真实 holdout。
> 新增 31 个测试 case，完整 Python `411 passed`。公开 V2 schema、Web、Graph 和 Prompt 未改动。

## 1. 本阶段解决什么问题

Phase 5C 的 48 条 development 对照证明了组件管线可运行，但不能自动推出两件事：
新数据真的未见／标注可信，以及模型识别后能否正确进入多轮工作流。

本次分别补齐：

- Eval 内的 `understanding-review` / `understanding-freeze`，建立候选数据、人工审核、
  已见语料检查和冻结产物之间的绑定；不是再增加一个模型或在线服务。
- 13 场景／28 Turn 的 V2 development 回归，关联 11 条组件样本，真实执行 HTTP
  契约、DeepSeek Adapter、LangGraph、SQLite 和既有五类 Tool；供应商响应由独立 Mock
  脚本提供，不由 Eval Gold 动态生成。

模块边界沿用 [ADR-0010](adr/0010-understanding-component-evaluation.md)：Eval 生产代码
不导入 Assistant；仅应用侧集成测试装配系统，再让 Eval Client 通过 ASGI HTTP 协议调用。
这不启动 TCP 服务，不包含反向代理、浏览器、真实供应商网络或生产并发验收。

## 2. 候选数据 → 人工审核 → 冻结

候选和参考文件都使用 `qu-case-v1`。新候选暂保留 `development` / `draft`，不要在审核
前手动改成 holdout，也不要先试模型再把成功样本选进测试集。`--against` 可重复指定，
最多 20 个参考文件；应包含所有已见的同格式语料。旧 V2／其他格式语料还需人工对照，
不能把未提供的参考集视为“已检查”。

```bash
uv run --package spb-eval spb-eval understanding-review \
  --dataset eval/datasets/private/qu-next/candidates.jsonl \
  --against eval/datasets/query-understanding-development-v1.jsonl \
  --output-dir eval/reports/qu-next/review
```

生成 `audit.json`、全部为 pending 的 `review.json` 和审核说明。审核包只带 ID、
逐条指纹和稳定问题码，不复制 query／Gold；审核者在原候选文件中按 ID 检查标签。

| 自动检查 | 冻结行为 |
| --- | --- |
| ID、规范化输入、声明的语义 group 与已见语料重复；seen_smoke | 有冲突的样本不能 approve，可明确 exclude |
| 候选／参考文件 SHA256 或逐条 case SHA 变化 | 拒绝过期审核，需重建审核包 |
| 缺失／重复／未知 ID、pending、无理由 exclude | 拒绝冻结，不能静默漏样本 |
| 审核者别名、带时区时间、标签和语义近重复检查未确认 | 拒绝冻结，不自动代签 |
| 批准后无样本，或目标目录已存在 | 拒绝写入，不覆盖历史产物 |

人工需检查意图、完整硬槽位、缺槽、控制／多意图口径；同义改写、相似模板、同一会话
切片应按语义 group 隔离。自动检查只有规范化精确输入和声明的 group，不是语义去重
模型。填写非敏感 `reviewer_id`、`reviewed_at`，确认两个检查项，并逐条选择 approve／
exclude（后者填写稳定 `reason_code`）。若改 Gold，重新生成审核包，不修改哈希绕过检查。

实际审核完成后运行：

```bash
uv run --package spb-eval spb-eval understanding-freeze \
  --dataset eval/datasets/private/qu-next/candidates.jsonl \
  --against eval/datasets/query-understanding-development-v1.jsonl \
  --review eval/reports/qu-next/review/review.json \
  --output-dir eval/datasets/private/qu-next/frozen
```

输出 `holdout.jsonl`、不含 Gold 的 `requests.json`、包含排除决定的 `review.json` 和
`manifest.json`。Manifest 记录源数据／参考集／审核／冻结数据／请求指纹、标签支持数和
硬实体数量；缺少意图类别会显式列出，但冻结本身不是六类质量门禁。评分仍使用 Phase 5C
的独立契约和指标，调用模型仍须另获预算授权。

这是**数据冻结**，不是代码／Prompt／运行配置冻结；模型实验前还需固定这些版本，
并在对照报告检查差异。审核身份是声明而非认证，文件权限不是防篡改签名；旧
`understanding-prepare` 手工入口仍保留，新工具不声称建立了不可绕过的权限系统。
所有失败校验在输出目录创建前执行；文件系统写入失败可能留下不完整目录，应使用
新路径重建，不能将残缺产物当作有效冻结。输出目录以 0700 创建，私有数据仍需 Git 忽略。

本次仅作污染诊断：将现有 48 条 development 与自身比较，48 条全部标记冲突，审核
全部保持 pending。没有人工审核的新样本被批准；该诊断不是一份 holdout。

## 3. V2 同场景离线回归

数据：`eval/datasets/agent-understanding-workflow-development-v1.jsonl`。
新 Agent Eval split 支持 `development`；旧 calibration/holdout 语义与历史夹具保持不变。

| 场景 | 验证内容 |
| --- | --- |
| 口语政策／设备问题 | 复用 V1 Tool、公开 Evidence；设备原问题不由模型改写 |
| 轨迹、分步地区、地区后补重量 | 模型识别 → interrupt → 当前上下文规则提取 → 历史槽位合并 → 完成 |
| 口语时限带两个地区 | 模型只补意图，地区仍由规则重提 |
| 正常 unknown | 安全 handoff，不生成业务结果 |
| 多意图、地区／重量歧义 | 先澄清，再补槽，再确定性路由 |
| 取消、tracking → postage | 控制终止、意图切换必须确认 |
| 已填地区冲突 | 不静默覆盖，confirm_overwrite 后才使用新值 |

独立供应商脚本含一次编造邮件号：真实 Hybrid 会丢弃它，V2 仍要求用户提供邮件号。
每个逻辑消息额外重放一次，比较除 request ID 外的公开响应相同，并确认没有新增模型
调用。另有三个测试关闭并重新创建整个应用／SQLite 连接后重放首轮、补槽和重放结果，
恢复进程中的 Mock Provider 调用数为 0。这不证明多副本或供应商计费 exactly-once。

故障回归向 Adapter 返回 429 或非法结构：HTTP 可以安全返回 handoff，但成功路径的
Eval 门禁必须失败；幂等重放不能触发隐式重试。不会因 API 返回 200 就算任务成功。

## 4. 验证证据与复现

| 指标 | 本地结果 |
| --- | ---: |
| 场景／逻辑 Turn 通过 | 13/13、28/28 |
| 等待用户时缺槽／澄清输入正确 | 15/15 |
| Wrong Tool | 0/11 |
| 目标任务完成／多轮恢复 | 11/11、9/9 |
| API Error | 0/28 |
| 额外幂等 HTTP 消息重放 | 28 次，未增加模型调用 |
| 供应商脚本调用／付费模型请求 | 9 次 Mock／0 次付费 |
| 完整 Python 测试 | 411 passed（本阶段新增 31 个） |

这些指标是受控集成回归，不是模型准确率或独立 holdout。测试脚本只提供固定 Mock
transport，报告显式标 `scripted_model_v2_regression`。不要将其 token／毫秒数字与
Phase 5C 真实供应商用量和延迟混合。

```bash
.venv/bin/python -m pytest -o addopts='' -q \
  eval/tests/test_understanding_review.py \
  apps/assistant-api/tests/test_phase5d_understanding_v2.py

.venv/bin/python apps/assistant-api/tests/test_phase5d_understanding_v2.py \
  --output-dir eval/reports/understanding-phase5d/v2-fixture
```

完整报告保存在 Git 忽略的 `eval/reports/understanding-phase5d/`。
可提交摘要见[Phase 5D baseline](../eval/baselines/phase5d-scripted-provider-v1/manifest.json)。
数据 SHA256：`1047d400f8e3d724f4333ac09ed636394f261e71e2a96dc0c6a153b081b58eb5`。

首轮 development 校准记录：设备样本原先误标为 need_more_info；核对既有解析／排名代码
与公开响应后，确认单商品 fixture 会根据品牌及 Pro 等词返回候选，已校准为 success 并
标记 `weak_device_identity`。没有修改匹配器、Prompt 或门禁来提高分数；该样本不能证明
中文代数被精确解析，也不能证明多商品库中型号唯一性。设备困难负例仍须单独验证。

## 5. 下一检查点

1. 由实际审核者提供／审核新的独立候选集，按分组和困难负例覆盖要求冻结；不能由
   自动化填写“已人工审核”。完成后另获模型预算，进行一次性真实对照及对应 V2 联调。
2. 等待人工数据期间，可继续原计划中不依赖模型 Key 的逐 Node wall-clock span、
   OpenTelemetry exporter／采样与 Dashboard；这不等于 holdout 验收已经完成。
3. 真实物流合同到达后完成 3B；V2 正式装配／发布和多副本硬化仍分别待验收。
