# Phase 3B-P / P1：资费领域契约与可执行条件

> 日期：2026-09-09。状态：P1 本地实现与合成回归完成；本文保留 P1 当时证据。
> 后续 [P2 协议与离线 Gateway](agent-kernel-phase3b-postage-p2.md) 已完成；真实互通仍未确认。
> 新增 86 项 Python 测试，全量 `727 passed`；Web `29 passed`，生成类型 / TS / 构建通过。
> 本轮先提交上一阶段：`843ae2e feat: complete controlled tracking and node telemetry`，未推送。
> 随后的 P1 改动尚未提交；没有修改用户 `.env`、物流真实请求或新增付费模型调用。

承接 [资费文档评审](agent-kernel-phase3b-postage-analysis.md)。新接口的产品字典、签名、
响应嵌套、返回单位和费用组成仍未获供应商确认。以下只证明明确的合成条件下工程路径
可执行，不能记为真实询价或 CSB 互通。关键决策见 [ADR-0014](adr/0014-explicit-postage-pricing-context.md)。

## 1. 已交付的边界

| 模块 | 本阶段职责 |
| --- | --- |
| `domain/postage.py` | 定价上下文、金额口径、费用项、来源及精确整数克转换；不依赖供应商 / LangGraph |
| `domain/postage_observation.py` | 成功报价观察：明确依据、计费重量、可信来源、带时区观察时间 |
| `services/postage_preflight.py` | 显式产品 / 地区目录、范围与重量前置检查、上下文冻结和恢复校验 |
| `workflow` | 注入前置策略，在原图中补产品、确认冲突、校验配置快照；不新增自由 Agent Loop |
| `tools/postage.py` / Result Validator | 严格询价分支要求新观察契约；执行前重查上下文，写收据及重放时校验事实 |
| `api/agent_schemas.py` | 不自动公开新增内部报价依据 / 计费绑定；现有公开投影保持兼容，完整公开设计在 P3 |

`create_agent_runtime` / `create_persistent_agent` 新增可选 `postage_preflight` 注入参数。
同时注入 Gateway 与前置策略才进入 P1 严格询价分支；此分支的 Descriptor 要求
`origin / destination / weight / product_code`，版本为 `phase-3b-p1`。
当前 `configured_agent.py` **没有注册真实资费 Gateway**，没有新增环境开关或自动装配。
没有 Gateway 时仍先返回 unavailable，不继续索要产品或地区。

未注入前置策略的 Phase 3A Fake 路径保留旧行为，供既有 Demo / 回归使用；它不是未来
真实资费 Adapter 的装配方式。P2/P3 的正式工厂必须显式建立、校验并注入前置策略。

## 2. 从“有槽位”到“可询价”

1. 沿用 Hybrid Understanding 识别资费意图、提取地区和重量；产品只由本地目录名称、
   别名或显式代码匹配，不接受模型凭空填入的产品码。产品未知或有多个候选时补槽。
2. 产品变更沿用 SlotMerger 的显式覆盖确认，不直接覆盖已经确认的产品；结构化
   RequiredInput 使用已有 `choice` 合同；公开 Web 产品控件在后续 [P3](agent-kernel-phase3b-postage-p3.md) 中验收。
3. 地名 `resolved` 不等于有计费绑定。地区的省 / 市 / 县层级需匹配显式目录；寄件地允许
   已配置地市下的县区使用该地市绑定，寄达地按目录粒度精确匹配，不自动回退到省码。
4. 原始 WeightValue 保留用户单位；另做精确整数克转换。`1.25 kg → 1250 g`，不自行
   进位或四舍五入。转换不依赖环境 Decimal precision；领域层拒绝非有限重量。
5. 识别出的体积、增值服务、客户 / 渠道优惠、国际和比价条件不被静默删除；当前仅支持
   国内指定产品、按实重、无增值服务，超出范围则终止并要求重新确认条件。

范围识别是**保守规则，不是完整语义理解**：有限关键词不能覆盖所有改写；例如“不需要
保价”也暂时触发范围复核，不宣称已实现否定作用域理解。产品和计费地区还受显式目录
约束，但这不能代替真实场景评测；P3 前应完善结构化条件确认，再测试遗漏与误拦截。

特定危险数字表达（科学计数、千分位、中文数字、未知重量单位等）当前拒绝，而不是
把尾部数字当重量或沿用上一轮重量。例如 `1,500 g` 不能误取成 `500 g`；等待选产品时
用户改成 `0 kg / -1 kg / 0.1 g` 也不会继续用旧的合法重量生成报价。

## 3. 明确定价上下文与报价依据

`PostagePricingContext` 在生成参数指纹前确定：

- 目录版本及内容 SHA-256 指纹；同版本号下更改目录内容也能检测；
- 产品、寄件 / 寄达计费绑定、精确整数克；
- 明确币种、`total / standard / customer` 金额口径和基础询价范围。

这些枚举仅表示契约可以描述的口径，不表示客户价功能已开放。上下文没有 endpoint、
AK/SK、password、客户代号或 CSB wire 参数；计费绑定只在内部使用。

新报价必须有：正的有限计费重量、最多两位小数的有限非负金额、与上下文一致的产品 /
币种 / 金额口径、带时区时间，以及唯一的类型化来源。`PostageQuoteBasis` 可以携带
费用项及“已含 / 未含 / 未知”的声明，但不自动相加，也不把缺失 / null 转成 0。
空费用列表表示未提供明细，不表示所有费用都是零。零金额只能是显式值，真实接口的
零金额业务语义还要由 P2 合同决定。

旧 `PostageData 或 None` 仅保留在历史 Fake Port 兼容分支；严格路径必须返回
`PostageQuoteObservation`。因此尚未确认的业务拒绝不会借用 None 变成“无报价”。
具体 CSB / retCode / errorcode 到业务失败的映射不属于 P1，后续由 P2 实现。

非法观察不落执行收据；有效结果在写收据、重放和最终回复时继续经过 Result Validator。
合成目录的报价必须标为 fake 来源，文案明确“合成演示 / 试算，不是最终支付价”。来源是
本地 Adapter 的配置声明，不是密码学认证或供应商成功互通证明。

## 4. Stateful Workflow 与兼容处理

新增 State 字段：`postage_policy_snapshot` 和 `postage_requirements`。
前者在本次资费输入经过检查时记录目录指纹，后者只保存固定范围 / 非法重量标签，不保存
原始补充文本。继续使用 State v3 的增量兼容：未强行填充旧状态的“已检查”证明。

- 新查询由 ingest 重置字段；取消 / 重置也清空它们。
- 补槽、重试与 SQLite 重启恢复保留快照；目录 / 币种 / 金额口径改变时返回安全的
  `postage_context_changed_restart`，不为旧查询重新默认选产品。
- 旧已暂停资费查询可能已经丢失原始附加条件，不能仅凭本次补槽文本重新认证：要求
  重新发起查询。旧待执行命令缺少新上下文时，严格 Tool 在 Gateway 调用前拒绝。
- 旧完成结果仍按 legacy 合同读取，不伪造新报价依据；未设置上下文的旧命令在计算
  fingerprint 时排除新增的 null 字段，保持既有 action / receipt 参数指纹一致。
- 新查询相同条件仍重新取数；已经保存的同次执行重放保留原观察时间且不追加调用。
  已完成历史报价可以原样重放，不等于它仍是当前有效价。

没有新增 SQLite 表或执行新的破坏性数据迁移。真实库仍需要备份 / 回滚演练；本轮测试
仅使用临时 SQLite。有限重试继续由图独占，测试一次合成超时后的重试保持相同定价上下文，
不宣称跨进程 exactly-once 或供应商只计费一次。

## 5. 验收证据与复现

`apps/assistant-api/tests/fixtures/postage_p1/` 的 manifest 明确标记 synthetic，包含目录与
成功领域报价样例；不是供应商 wire 报文或签名向量。产品为 `SYN-A / SYN-B`，计费 ID
带 synthetic 前缀；地区来自既有 Demo Resolver。

该 fixture 显式选用 `CNY / total / 30000 g`，金额 `12.30` 和计费重量 `1500 g` 由测试
文件提供，不通过重量公式生成。它们都是测试条件，不是邮政真实规则。领域另外设置
1,000,000 g 和 999,999,999.99 的防误配置 / 金额上限，同样不代表供应商业务限额。

新增 86 项测试覆盖：

- 整数克与异常数字、目录缺失 / 冲突、地区绑定、产品和保额边界；
- Decimal 精度、费用唯一性、缺金额 / 单位 / 计费重量 / 来源、配置指纹和 legacy 指纹；
- 两种持久化后端的产品补槽 / 冲突确认、新查询新鲜度和 checkpoint 收据重放；
- SQLite 重启、同版本目录变更、旧查询要求重启、取消、模型虚构产品、未开放能力；
- 非法报价不写收据、单次图重试、合成来源不能升级成 external、内部绑定不外露。

```bash
LANGGRAPH_STRICT_MSGPACK=true .venv/bin/pytest \
  apps/assistant-api/tests/test_phase3bp_postage_contract.py \
  apps/assistant-api/tests/test_phase3bp_postage_workflow.py -o addopts='' -q
# 86 passed

LANGGRAPH_STRICT_MSGPACK=true .venv/bin/pytest -o addopts='' -q
# 727 passed（本轮前 641）

cd apps/chat-web
npm test
# 29 passed
npm run check:agent-types
npm run build
```

本轮未改变 Web 代码或新增公开 DTO 字段，没有重跑浏览器交互；P1 的报价依据暂不公开，
现有 API 仍返回原资费字段。不能将本轮 Python 图内测试写成新版资费 UI 已验收。

## 6. P1 当时规划与后续进展

P1 规划的下一切片在不发真实请求的条件下完成：独立资费 wire schema、明确且可替换的 provisional
响应嵌套、业务 / CSB 两层签名、一次序列化 / 编码、四层成功门禁、错误映射与语义熔断。
默认关闭，不猜测式试签名，不绕过 TLS 验证，不把本地自制向量当供应商确认。

P2 已将 provider profile 与目录 / 金额语义关联，并对 P1 目录字段到 wire 字段的映射
独立测试，详见 [P2](agent-kernel-phase3b-postage-p2.md) 和[缺口台账](agent-kernel-phase3b-postage-gaps.md)。
P3 再做受控装配、公开报价依据 / 来源、Web 和 Eval 的同步
演进。P4 真实联调及轨迹 T4 保持暂缓；当前不需要 API Key。
