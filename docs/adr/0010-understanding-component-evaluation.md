# ADR-0010：Understanding 组件评测与 Workflow 黑盒验收分层

- 状态：Accepted / Phase 5C implemented
- 日期：2026-09-07
- 补充 ADR-0008，不改变既有 V2 多轮黑盒评测边界。

## 问题

V2 返回业务状态、补槽要求和结果卡片，不暴露完整 QueryUnderstandingResult。用
`required_inputs` 正确率代替 Slot F1 会混淆“知道缺什么”和“实体值提取正确”。为测量
组件而给生产 API 暴露槽位、原始 State 或 debug endpoint，又会扩大协议和隐私边界。

另一方面，模型超时后回退到规则 unknown 可能得到正确的业务终态，却不是模型识别
成功。缺失 observation、预算跳过与未收到 usage 也不能按成功、零耗时或零成本处理。

## 决策

- 保持 `agent-run` / `agent-compare` 只调用 V2 HTTP；它们验证真实 conversation、
  interrupt/resume、状态合并、工具与结果投影，不读取 checkpoint 或组件文件。
- 新增独立组件路径：Eval 从标注数据导出**不含 Gold 的请求文件**，Assistant CLI
  调用现有 Understanding Port 并导出观测，Eval 通过独立文件契约评分。两应用的
  生产代码互不导入，不新增生产 HTTP 路由或 Eval 运行依赖。
- 样本输入可以包含已知 active/explicit intent 和 expected slots；每条独立执行。
  这是给定上下文的组件测量，不推导上一轮 Gold、不假装验证历史槽位合并。
- 六类 Intent（五业务 + unknown）使用固定标签 Macro-F1；控制与多意图／歧义行不
  强制单标签。候选集合、多意图、控制、缺槽提示分别校验。score 不当作概率。
- 硬槽位按邮件号、规范起寄地／寄达地名称、公斤重量四个原子值计算联合 micro-F1；
  错值计 FP + FN，多抽计 FP，漏抽计 FN，不用字符串 question 或字段个数充当实体 F1。
- 原始问题、Gold、Prompt、Key 和供应商正文不写入 observation；槽位值规范化后写
  指纹。指纹是可关联的假名化，不承诺对低熵值不可逆。私有数据与运行产物仍 Git 忽略。
- 数据和输入均绑定 SHA256，拒绝重复 ID、重复规范化输入、group 跨 split、非法契约
  和不匹配 observation。未审核／已见烟测样本不能标作 holdout；近重复仍需人工审核。
- 对照从同一完整 Gold 和原始 observation 重算，阈值一致；不信任已有 summary。
  保留配置／代码差异，不能将多变量变更的差异解释为单变量因果效果。
- 模型实验须显式启用、授权并给出请求上限。串行执行、每个 fallback 单次尝试，
  超出预算留 skipped；已完成行及时 flush，缺失行继续进入失败分母。
- Adapter 提供 best-effort、脱敏 call observer；组合根可装饰 Model Port，用于
  本地评测预算和测量，原 Client 仍由组合根唯一关闭，不改 Graph 或线上预算。
- 区分正常模型 unknown、模型失败后规则 unknown、预算 skipped 和完全缺失的行。
  Token 只累加已收到的合法 usage，同时列出未知用量／未观测行，不能当成最终账单。

## 验证与限制

Phase 5C 提供 48 条分层 synthetic development 数据、Rules 基线、Mock 对照与有界真实
模型实验入口。当前所有标签仍为 draft；这一数据集不是生产分布或未见 holdout。
公式以手工可验 TP/FP/FN 样本验证，并测试污染防护、失败分母、预算、中断和独立契约。

后续仍需人工审核新 holdout、冻结版本后进行一次性真实对照，以及 V2 同场景回归。
逐 Node OpenTelemetry、模型概率校准、设备规格抽取、完整地区库和生产 SLA 均不在
本切片内。见[Phase 5C 实施记录](../agent-kernel-phase5c.md)。
