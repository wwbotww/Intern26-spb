# ADR 0016：命令绑定的报价确认与独立公开报价依据

> 决策记录：下文验收范围保留为记录当时的事实；现状见[当前状态](../current-status.md)。


- 状态：Accepted / P3 offline verified
- 日期：2026-09-09
- 前序：[0014](0014-explicit-postage-pricing-context.md)、[0015](0015-provisional-postage-adapter.md)

## 背景

字段收齐不等于用户已接受询价范围；确认覆盖重量也不等于批准该重量对应的报价。
公开消费者需要判断金额口径、来源与估算限制，但内部计费 ID、策略指纹及认证身份
不应进入 API。轨迹的历史完整性不能解释报价的费用组成。

## 决策

1. 受控 P3 组合根要求显式报价确认。Policy 准备类型化 Command 后生成参数指纹；
   decide Node 将待确认指纹 checkpoint，复用 LangGraph interrupt / resume 暂停。
   只有在这一中断中收到独立的“确认基础询价”消息才授予本次确认，执行前再次比较
   Command 指纹。首次输入夹带确认、改条件夹带确认均不执行；覆盖冲突后再次确认。
2. 增加非敏感、由部署配置指定的 pricing_scope_ref，参与 profile / policy 指纹。
   密钥轮换本身不写入价格语义；计价主体或渠道变化必须调整引用。P3 只有合成身份，
   真实认证与计价主体的映射仍需接口方确认。
3. 新增可空的 result.quote_basis 白名单 DTO，包含 schema_version、金额口径、币种、
   产品、范围、估算标记、费用项和独立报价来源。删除 data 内部 quote_basis；不输出
   计费绑定、指纹、原始响应、认证信息，也不沿用 tracking.history_completeness。
4. JSON、SSE、OpenAPI 生成 TS、Web 实时及刷新解析、独立 Eval 镜像共同演进。
   旧响应缺依据时保持未知；不能补 CNY、免费或真实来源。费用包含关系未知就显示未知，
   不自行求和；观察时间在重放中保持原值。
5. 离线工厂显式注入 MockTransport，拥有 Gateway / SQLite / janitor 生命周期，
   忽略 dotenv 和环境配置，强制鉴权与绝对 SQLite 路径；默认 main 不装配资费。

## 权衡与证据

增加一次交互换取可见、可恢复的范围确认，并非通用审批平台或自然语言授权识别。
保守规则仍可能误拒“不需要保价”；不能靠确认掩盖上游字典、计价语义缺失。
新增 State 字段可空，确认模式参与策略指纹，旧未核验的暂停状态不能被自动认证。
P1 / P2 内部测试路径不强制确认，保留此前已验证行为；P3 入口必须确认。

验证清单、运行入口和未完成事项见 [P3 实现说明](../../apps/assistant-api/docs/integrations/postage.md)。
