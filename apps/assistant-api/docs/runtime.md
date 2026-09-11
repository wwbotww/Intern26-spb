# Agent 运行设计

[Assistant API](../README.md) · [项目文档导航](../../../docs/README.md)

用途：解释当前实现的不变量和扩展边界。进度见[当前状态](../../../docs/current-status.md)，字段事实以
[Domain](../src/spb_assistant_api/domain/)和[Workflow](../src/spb_assistant_api/workflow/)为准，
设计理由保留于 [ADR](../../../docs/adr/README.md)，不再维护第二套伪代码 schema。

## 1. 受约束 Agent 与 Query Understanding

目标是五类只读查询，不是自由 ReAct。模型不能指定工具名、函数、SQL、URL、重试次数或权限。
工作流一次处理一个明确业务目标；多意图先澄清，不并行拼出未经定义的综合结果。

`QueryUnderstandingResult` 使用 Pydantic 和 `schema_version="1"`：

| 字段组 | 用途 |
| --- | --- |
| original_query / normalized_query | 保留输入边界；不是允许模型改写业务事实 |
| selected_intent / candidates / multi_intent | 五意图 + unknown、候选与歧义；score 是启发式信号而非校准概率 |
| slots / slot_provenance / missing_slots / ambiguities | 判别式槽位、字段来源、缺失与冲突 |
| control | none / cancel / restart；确定性控制不交给模型 |
| source / parser_version / prompt_version | 显式入口、活动会话、规则或模型的可解释来源 |

处理顺序：

1. 识别控制、显式入口和等待输入上下文。
2. 规则提取意图与硬实体；可信结果直接使用。
3. 需要语义补充时才调用可选模型；一次有界请求，失败保留规则结果。
4. 校验模型输出后，按意图从原始用户输入重新提取邮件号、地区、重量等硬实体。
5. SlotMerger 合并本轮与历史槽位；冲突不静默覆盖，切换意图需确认。

当前 Prompt 要求模型不提供硬槽位。即使其返回格式合法的实体，也不能代替确定性重提。
RegionResolver 的名称 resolved 不等于供应商 code 可执行；当前内置目录仅用于 Demo。
配置与预算详见[模型接入](integrations/query-model.md)。

## 2. Routing 与执行门禁

`WorkflowPolicy` 不做 I/O，返回判别式 NextAction：
understand、clarify_intent、collect_slots、invoke_tool、validate_result、respond、handoff 或 control。

依次检查控制/预算、意图歧义、能力是否装配、槽位/冲突、领域前置条件，再构造 Command。
**未装配能力在补槽前结束**，不索取无法执行的查询所需信息。

Registry 静态白名单注册 Descriptor 与 Tool；Descriptor 声明意图、输入/输出、超时和尝试预算。
Executor 校验 Command/Tool 匹配、执行身份与结果；不能由模型输出任意 tool name。
政策/设备通过兼容 Adapter 复用 V1，其他能力通过领域 Gateway Port 执行。

## 3. Stateful Workflow 与 Agent Loop

~~~text
ingest → understand → decide_next ─→ clarify ⇄ interrupt / 下一消息 resume
                         ↑               │
                         └───────────────┘
                         │
                         ├→ execute_tool → validate_result → compose_response → END
                         │                        │
                         └──── recover ←──────────┘
                         └→ compose_response → END（控制 / handoff / 安全失败）
~~~

图装配在 `workflow/graph.py`；Node 返回增量更新，Reducer 合并有界事件/调用记录。
`clarify` 的恢复会重新执行节点，因此节点不得在 interrupt 之前做不可幂等的外部操作。
LangGraph 负责调度、条件边、checkpoint 和恢复；业务决策仍由 Policy/Service 完成。

State 分别保存会话/消息/逻辑查询身份、当前意图与槽位、待执行动作、结果/失败、
调用和事件记录、deadline 与步骤/Tool/重试预算。HTTP 输出只是这个状态的白名单投影。
不把 Client、Key、Prompt 正文、SDK 对象、原始供应商响应或活跃 span 写入 State。
用户输入与结果仍可能存入 SQLite，不能因为日志脱敏就声称状态库没有敏感数据。

约束同时由 Policy、Descriptor、运行总 deadline 和 LangGraph recursion limit 兜底。
共享 HTTP 每次仅尝试一次；业务瞬态故障默认最多两次实际尝试，由图独占重试预算。
模型 fallback 另有一次调用预算，不与 Tool 重试混算。

## 4. 三层幂等与查询新鲜度

| 标识/存储 | 作用 | 不能混同 |
| --- | --- | --- |
| conversation_id / thread_id | owner 约束下定位会话与 checkpoint | 不充当授权令牌 |
| turn_id、消息 Idempotency-Key | 标识一次用户消息；重放不再次推进图 | 一次查询可跨多轮补槽 |
| query_id | 跨补槽/重试稳定，主动新查询重新生成 | 不等于最新 turn_id 或永久缓存键 |
| tool_call_id | 查询作用域内的执行身份；收据按会话/调用查找 | argument fingerprint 只校验参数一致性 |
| 创建收据 | owner + 创建键 + 请求 hash → 随机会话 | 首次响应丢失不能重复建会话 |
| 消息收据 | 会话 + 消息键 + 请求 hash → 停止态响应 | 相同键不同业务内容返回冲突 |

同消息跨 JSON/SSE 重放保持原 turn 与观察时间，`stream` 不参与业务指纹。
用户再次查询相同单号或价格，使用新消息/逻辑查询重新取数，不能误用旧收据作为业务缓存。
执行前/写收据前、命中收据时、公开响应前均有相关合同校验，非法事实不成为可重放成功结果。

当前业务 State v3 和 receipt v2 保留兼容迁移。仅从可证明的旧 pending action 恢复执行身份，
不按相同参数猜测；旧表保留不代表可以直接用旧二进制打开新状态。
升级和回退使用一致快照及匹配版本，见[运维](operations.md)。

## 5. 记忆、身份与并发

- Checkpointer 保存工作状态；元数据保存 owner/状态/绝对 TTL；创建/消息/Tool 收据分别处理幂等。
- RAG 是外部只读知识，不是会话记忆；没有跨会话长期记忆或跨设备历史查询功能。
- 每会话 coordinator 串行推进/清理；managed 目录整库合作进程租约拒绝第二个实例。
- Browser Security 先验证代理服务 Key、Origin、签名 Cookie 和 session_ref，再进入会话服务。
  Graph 不读取 Cookie/Origin；其他可信服务 Key 仍为 key-scoped owner。
- 签名访客不是账号、RBAC 或资费客户资格。旧 key-scoped 会话不自动迁移给新浏览器身份。

## 6. Failure Handling

| 情形 | 行为 |
| --- | --- |
| 缺字段 / 意图歧义 / 冲突 | interrupt 等待输入，不调用上游 |
| 未装配 / 超出自动处理范围 | 安全 handoff；当前未连接人工工单系统 |
| 有效空结果 / 资料不足 | 正常完成的 no_match，不虚报技术故障 |
| timeout / 429 / 确认可重试的 5xx | 图在总预算内退避重试；不叠加 SDK 重试 |
| 非法 JSON、错单号、错金额/单位、合同漂移 | contract_violation；不展示事实、不换一种猜法重试 |
| 鉴权 / 状态冲突 / TTL / 持久化故障 | 稳定 HTTP 或流内错误；不自动换 owner / 新键掩盖冲突 |
| 取消 / 内部异常 | 释放容量及半开探测位，脱敏失败；遥测错误不覆盖业务结果 |

Postal Gateway 在完整语义校验后才记熔断成功，每次尝试只记一次。
已定义的资费业务拒绝可证明协议健康，但不是有效报价；其他业务码不靠错误文本猜可重试性。

不能承诺上游 exactly-once：上游执行后收据未提交、模型响应后 checkpoint 未提交、
硬崩溃残留 IN_PROGRESS 都有独立窗口。正常完成后的重放保护不等于跨系统事务。

## 7. 资费的特殊不变量

产品、供应商地区编码、整数克、计价主体/渠道引用、金额口径和 profile 先冻结，再计算指纹。
国内实重、无增值服务的范围需明确；不支持的条件不能被静默丢弃。
确认绑定当前 Command，而非永久布尔值；改重量/产品或配置漂移后旧确认失效。
来源和金额依据单独投影，金额用 Decimal；不补零、不猜币种、不自行合计含费未知的费用。
详见[资费契约](integrations/postage.md)。

## 8. 观测与评测

语义 Trace 解释路径；OTel 在实际 invocation/Node 边界计时；HTTP run 还包含收据重放。
interrupt 结束当前 span，下一消息创建新的 root，不把人类等待算作节点执行。
SDK/采样/exporter 由 Demo 或 configured 组合根管理；不注册全局 provider，不改变业务状态。

Understanding 文件评测测组件；V2 HTTP 评测测完整多轮行为；两者不能互相代替。
同样本对照校验 dataset/Gold/门禁指纹并从观测重算，缺失/失败不能移出分母。
操作入口分别见[运维](operations.md)与[Eval](../../../eval/README.md)。
