# 商品价格有界执行循环（D3）

[消费合同](product-price.md) · [理解与补槽](product-price-understanding.md) · [实施计划](../../../../docs/product-price-implementation-plan.md) · [验收状态](../../../../docs/current-status.md)

本文解释 D3 的内部 Tool、LangGraph 循环及恢复边界；后续 D4～D7 已完成配套公开协议、
客户端门禁、Web/Eval 与 State 4，见[协调发布设计](product-price-public-release.md)。
catalog_v2 的正式持久服务借用同一 Service；内部原始 stream_events 仍拒绝价格，
公开 SSE 通过带身份、幂等和白名单投影的消息入口执行。不得把隔离测试状态卷直接接到生产。

## 1. 职责与事实流

```text
结构化条件 → Policy → ProductPriceTool → 共享 ProductPriceQueryService → V2 Repository
                         ↓                    ↑ 精确重读同一身份
                 校验结果 / 保存收据          │
                         ↓                    │
                唯一命中：完成       多候选：checkpoint → interrupt → 选择验证
```

- Tool 借用 C 的同一 Service，不拥有数据库连接池，也不复刻 V1 查询实现。
- Service 新增 `quote_product`：设备继续使用共同的硬身份/规格策略；生鲜用有界来源查询和精确条件过滤。
- `ProductPriceData` 在本阶段只是内部执行事实与收据模型，包含内部标识；**不得直接发送给浏览器**。
  D4 的公开投影必须按字段白名单重新构造，不能直接 `model_dump()`。
- `need_more_info` 经 validate_result 回到 decide_next；不再无条件结束，也不当作故障重试。
  缺少可操作补充字段、模式/状态不符、来源不一致或事实不满足条件的结果在保存收据前拒绝。
- 无金额状态仍是命中，保留 null 与状态事实；不造零元，不借历史金额填充当前价格。
  `today` 以各次读取完成时刻的中国日历日核对来源日；不符合时用 partial /
  `price_today_not_available`，保留真实观察和警告。新鲜度未知/超期不被伪装为实时。

实现入口：[Tool](../../src/spb_assistant_api/tools/product_price.py)、
[共享 Service](../../src/spb_assistant_api/services/product_price_query.py)、
[结果校验](../../src/spb_assistant_api/services/result_validator.py)。

## 2. 生鲜语义边界

来源目录独立于物流地址解析。`fresh_price_scope.py` 中商务部省级名称/码来自已审查的采集侧
`7191523` 源码快照，不在运行时读取另一工作树，不把“有码”解释为“有实时数据”。

| 请求 | 读取与结果边界 |
| --- | --- |
| 上海零售 | CITY/310100，RETAIL_AVERAGE，保留原价 CNY_PER_500G 与标准 CNY_PER_KG |
| 上海批发 | PROVINCE/310000，WHOLESALE_AVERAGE；不是上海零售同一统计口径 |
| 北京等省级批发 | 商务部来源地区码；兵团保留 XJ_CORPS，不猜成新疆省级码 |
| 上海、明确分别列出来源 | 最多两次有界 Repository 读取，结果逐事实独立，不混算均价/最低价；这仍是一次逻辑 Tool 调用 |
| 未指定地区但明确分别列出来源 | 单次有界召回；保留每个来源地区/市场/口径，截断时明确范围有限 |
| 区县/未知城市，或非上海零售 | 不放宽为全省/上海；返回稳定的范围不支持提示 |
| 品名/品种/市场 | 品名与市场名称精确匹配；品种仅与来源规格原文精确对应，暂不猜别名、市场 ID 或品种分类 |
| 斤/公斤/克 | 保留请求数量/单位与原始/标准价事实，D4/D5 已配套公开展示；按个或无包装依据的箱/盒不推算重量 |

文本召回可能较宽，但候选还要经过事实条件校验。若有界召回被截断且未找到精确条件，
返回需要更具体条件，而不是宣称数据库里没有该商品。市场别名、品种规范化和更广自然语言地名
仍是覆盖缺口；不属于已完成通用中文 NER。
来源选择的“多个来源”不是按个计价；“分别列出多个来源”或“零售”也不会因当前正等待地区补槽而被当作地名。

## 3. 候选绑定与精确重读

候选只保存在本会话 checkpoint，最多展示 20 项；没有跨用户候选缓存。
每个 token 使用 32 随机字节，不接受模型提供的 ID，也不将 JSON 藏进 choices 字符串。
公开 interrupt 现已提供 token/label/expires_at DTO；choices 仅保留序号和展示标签，不保存身份 JSON。

选择须同时满足：owner、conversation、query、语义条件指纹、候选集合成员、有效期。
有效期是“生成后 10 分钟”与调用方提供的可信会话到期时间的较早者，不因恢复而续期。
内部 Runtime 需要调用方传入可信 owner/到期时间；D6 已连接正式会话元信息与公开身份边界。
`PriceSelectionInput` 只接受 candidate_token，不能与改题、覆盖或其他意图混合；
纯序号回复如“第二个”只解析当前候选表，不交给 QU/LLM 生成身份。

验证通过后才构造 `PriceSelectionReference`。它绑定 listing、地区、revision、规范身份/规格、
来源渠道和计价基础；金额、观察时点、可售状态和链接不是商品身份。
`SelectedPriceReadQuery` 在一个新的只读事务里直接读取该 listing/地区的当前指针，不重跑模糊召回。
相同身份允许读取更新的价格；身份或 revision 变化、当前指针消失时要求重新查询，不换相似商品。
重复当前行或非法关联属于 contract violation，不伪装为普通未找到。

条件合并输出的失效标志现在实际清掉候选、待选择 token 和结果补槽要求。
未确认覆盖仍保持原条件；补充/确认新条件可以使用剩余调用预算，但不偷偷创建新 query 来刷新额度。
要重新开始预算，须结束/取消旧查询后发起新查询。

实现：[内部模型](../../src/spb_assistant_api/domain/product_price_execution.py)、
[候选策略](../../src/spb_assistant_api/workflow/price_candidates.py)、
[精确读取 SQL](../../src/spb_assistant_api/adapters/mysql_product_price.py)。

## 4. 预算、恢复与收据

| 预算 | 作用域与行为 |
| --- | --- |
| 2 次逻辑 Tool 调用 | 价格 query 专属：发现/直接报价，之后选择或补充条件后的查询；其他业务默认仍为 1 次 |
| 1 次技术重试 | 整个价格 query 共用；仅现有暂时故障白名单允许，不因换到第二次逻辑调用重新获得 retry |
| 3 轮澄清 | 包括初始缺槽与结果候选；第三轮回答若能完成则可执行，仍需继续询问时明确结束 |
| 恢复 | 保留 query_id、Tool/retry/澄清计数和候选到期；只刷新本轮 deadline/turn，普通 start 消息在 pending 时转 resume |
| 收据 | 选择引用进入 Command 指纹，发现与最终报价形成不同 call_id；相同完成 call 返回原事实，新 query 才重新取数 |

第二次调用仍返回多候选时不能再诱导第三次调用；直接以预算错误结束。
每个 call 的 attempt 编号与 query 级 retry 计数分开；一次正常不中断执行最多 3 次 Tool 实际尝试。
保留现有请求、Graph step/recursion 与数据库超时边界；不宣称硬崩溃窗口下的 exactly-once。

内存和 SQLite 关闭重开测试均保留候选/收据并只执行最终精确重读。
这只验证 D3 新查询的内部恢复，不替代 D6 的旧版本迁移、消息级幂等、公开 JSON/SSE、浏览器刷新和双访客验收。

## 5. 验证与下一阶段

- [内部工作流测试](../../tests/test_product_price_agent_loop.py)：单轮/多轮、无金额、过期/串作用域、条件失效、2/1/3 预算、SQLite 恢复、收据重放及公开门禁。
- [生鲜执行测试](../../tests/test_product_price_fresh_execution.py)：地区、批零、市场候选、单位、原始时间及不放宽约束。
- [真实 SQL 隔离测试](../../tests/test_product_price_mysql_integration.py)：MySQL 5.7/8、V2-only SELECT、选中身份精确重读/当前价格推进/指针删除。

以上只使用合成数据。本轮没有查询生产库、写业务表、调用付费模型或部署。
最新执行结果集中在状态页，不在此复制计数。

后续交付：公开白名单 DTO、结构化候选输入、`X-Agent-Contract: product-price-v1` 门禁、
OpenAPI/TS/Web/Eval 和 State 4 已配套实现。实际 CI/发布证据见状态页；D3 当时的验证不自动等于线上验收。
