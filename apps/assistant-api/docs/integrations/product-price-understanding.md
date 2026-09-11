# 商品价格 Understanding 与补槽（D1/D2）

[消费合同](product-price.md) · [实施计划](../../../../docs/product-price-implementation-plan.md) · [当前状态](../../../../docs/current-status.md)

这里描述 D1/D2 理解组件。后续 catalog_v2 装配已配套统一公开入口、候选协议、Web/Eval 与 State 4，
见[协调发布](product-price-public-release.md)；D3 内部循环见[执行设计](product-price-agent-loop.md)。
独立旧环境的默认 profile 仍为 device_price，不能只改客户端而忽略服务端装配和状态升级。

## 类型与职责

- `ProductPriceSlots.conditions` 以 `kind` 区分 `unknown/device/fresh`；不允许在一个对象中混放商品规格与寄递重量。
- 设备：品牌、产品文本、容量/内存、已提取的颜色/尺寸/连接方式；规格筛选最终还需查询核心验证证据。
- 生鲜：品名、品种、原文地区/市场、批零口径、来源范围、期望计价单位。
  地区不是物流 Demo 的行政区代码，市场名也不是模型生成的数据库 ID；D3 来源解析再验证是否支持。
- `PriceUnitRequest` 保存请求数量、单位与原文；`500g`、斤、公斤不在理解阶段折算成报价金额。
  箱/盒等缺乏包装依据的要求保留为 unsupported，不能悄悄改成公斤报价。
- `PriceTimeConstraint` 区分最新可用观察、今天与未支持的日期/趋势。今天要求进入 Command，
  后续结果校验仍要核实来源日期；最新可用不等于实时。
- `ProductPriceCommand` 使用完整分类条件与时间约束，没有 question 执行参数或 SQL。
  D3 增加服务端验证 token 后生成的 selection 身份引用；它不属于 QU 槽位或模型可生成的字段。

实现：[槽位/Command](../../src/spb_assistant_api/domain/product_price_slots.py)、
[抽取规则](../../src/spb_assistant_api/services/product_price_understanding.py)、
[能力与输入检查](../../src/spb_assistant_api/services/product_price_preflight.py)。

## 理解和路由

统一组件使用 `RuleBasedQueryUnderstander(product_price_enabled=True)`：规则先判业务，
Structured 模型仍仅补充语义分类；模型 Schema/Prompt 使用对应 profile，随后从原句重提硬实体。
模型给出的商品、品牌、规格或地区不能替代规则证据，也不能生成执行标识。
默认 profile 不接受 product_price；统一 profile 不继续执行旧 device_price 上下文。

“苹果多少钱”先补类目；“苹果一斤/1kg/500g 多少钱”进入生鲜，缺地区或批零口径时补齐。
有明确型号/容量证据才进入设备；单独的 G/g 不能当作 GB，尤其不能把黄瓜 500g 识别为设备容量。
寄苹果的费用仍是 postage；否定运费后的商品价格纠错不被旧寄递意图锁住，跨意图仍需原有确认流程。
明确同时查询商品价格与运费时保留多意图选择。

商品计价数量不再独立触发 postage；活动 postage 的重量补充仍有效。
无法执行的能力先返回不可用，不先索取地址、商品或重量。多规格、多地区、多品名等歧义进入输入检查，
不能因为其他槽位已齐而直接执行。

这些是有边界的确定性规则，不是通用中文实体识别：品名、地名、市场/品种和规格表达覆盖有限。
当前已明确支持的多来源请求可跳过强制唯一地区/批零条件，但结果仍须逐来源保留口径，不做跨口径均价或最低价。
遇到未识别的已知市场限定会要求补充，不宣称任意市场都已接通。

## 合并、冲突与失效

[价格合并策略](../../src/spb_assistant_api/services/product_price_slot_merger.py)由现有 SlotMerger 分发，使用语义字段路径：

| 变化 | 确认与处理 |
| --- | --- |
| 未知类目 → 明确类目 | 保留可证明的“苹果”主题，不猜设备型号 |
| 已确认类目变化 | 原子拒绝整个更新，确认后替换联合类型，清掉旧类别条件 |
| 型号/品牌变化 | 确认后清掉旧规格；只有新品牌而没有型号时重新补型号 |
| 生鲜品名变化 | 确认后清掉旧品种与计价单位；仍适用的地区/批零条件保留 |
| 地区变化 | 确认后清掉旧市场，不把原市场带到另一个城市 |
| 单一口径扩展为多个来源 | 涉及已确认地区/市场/口径的放宽时先确认，再清理没有被新请求保留的范围 |
| 容量/内存/颜色等补充 | 按字段合并；已确认的值变化需确认，不能整个覆盖嵌套对象 |
| 任一语义条件变化 | 返回 `invalidate_price_candidates=true` 与失效字段；D3 已消费该标志清理候选 |
| 同义单位原文或同义时间原文 | 保留已有值，不刷新候选或改变 Command 语义指纹 |

未确认冲突不部分合并新规格，避免旧型号搭配新规格。模型来源即使遇到 blanket confirmation
也不能覆盖已确认值；同值优先保留更强 provenance。理解组件本身不保存候选；D3 在 Workflow 中保存/校验 token。

## 验证与安全开放边界

固定合成集为 [24 条 development 输入](../../../../eval/datasets/query-understanding-product-price-development.jsonl)。
组件导出使用既有 `spb-assistant-understanding-export --product-price`，规则模式不读配置、不调用模型或数据库。
Eval 独立镜像语义字段，仅接收字段指纹；不修改原 48 条样本或 A 的设备 Gold。
标签集合由 Gold 决定：旧五业务类、统一价格五业务类、混合历史类分别记录，
避免新增一个未使用标签就改变历史 Macro-F1；不同标签集合的结果不可直接当作同口径提升。

Mock 模型测试覆盖 profile 拒绝、幻觉字段丢弃与错误计数；不代表已做新一轮付费模型质量实验。
测试：[理解/路由](../../tests/test_product_price_understanding.py)、
[条件合并](../../tests/test_product_price_slot_merger.py)、[独立 Eval](../../tests/test_product_price_understanding_eval.py)。

OpenAPI/生成 TS、公开 DTO、Web 校验和 Eval 已配套 product_price。
catalog_v2 的浏览器门禁要求 product-price-v1；旧价格 pending 不猜测迁移，State 4 只保留旧完成事实。
运行装配、回归与实际部署是不同验收层级，最新证据见状态页。
