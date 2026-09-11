# 数据源与消费边界

用途：说明数据由谁生产、应用实际消费什么，以及扩展时不能混淆的版本。
数据数量与最新核验时间只在[当前状态](current-status.md)或有日期的历史证据维护；
本文不提供数据库凭据，不要求接手者重新导入已有数据。

## 1. 三种“V1/V2”不是同一版本线

| 名称 | 含义 |
| --- | --- |
| Assistant API V1 / V2 | 单轮显式工具入口 / 有状态 Agent HTTP 协议 |
| 价格数据 V1 / V2 | 设备品牌/产品/SKU 价格结构 / 全品类 listing 与观察结构 |
| Agent State / receipt 版本 | checkpoint 与执行收据的恢复兼容合同 |

默认 Agent V2 通过兼容 Tool 消费价格数据 V1；catalog_v2 的显式受控配置通过同一报价核心消费 V2。
支持 API V2 **不表示**选择了价格数据 V2，更不表示所有规范商品数据均已填充或统一商品意图已经开放。

## 2. 数据职责

| 数据 | 生产者 | 消费者/契约 | 写权限边界 |
| --- | --- | --- | --- |
| 政策文档与向量 | 本仓库 offline-pipeline | rag-api / packages/contracts | 离线可建表/写入，RAG 只读 |
| 设备价格 V1 | 独立 device-price-service，不在本 uv workspace | Assistant MySQLPriceRepository / DevicePriceRecord | Assistant 仅 SELECT，不采集/修复数据 |
| 全品类价格 V2 | 同一独立价格数据项目 | MySQLProductPriceRepository / ProductPriceQueryService；设备受控薄适配，生鲜仍为内部读取 | 仅 SELECT；真实兼容仍需 E 核验，不复用写入权限 |
| Agent 会话 | Agent Runtime + 元数据/收据仓储 | LangGraph / 会话服务 | 专用 SQLite，与业务价格库分开 |
| Eval 数据/报告 | 审核者与独立 Eval | HTTP/文件契约 | 私有样本/运行报告不提交，不直接读业务库 |

RAG 的连接、collection、schema 和 embedding 要一起核对；
设备价格 DSN 必须指向获准环境，不能因为查询失败自动尝试其他库或扩大账号权限。

## 3. 设备价格 V1

消费产品、SKU 和 price_current 关联，包含品牌、产品级型号/系列、SKU 规格、
当前/原价、币种、渠道、来源 URL、在售状态和观察时间。
实现见[MySQL Adapter](../apps/assistant-api/src/spb_assistant_api/adapters/mysql_price.py)，
匹配语义见[V1 接口](../apps/assistant-api/docs/api-v1.md)。

顺序：产品级硬身份约束 → 相似度排序 → 该产品内规格过滤。
目标要求明确型号/Pro/容量不符不能用“相似价格”替代；历史型号未覆盖时 no_match 是正确结果。
迁移基线暴露的 Pro/Pro Max 替代与基本款/Pro 混合缺陷由共享策略修复，
详见[消费合同中的回归边界](../apps/assistant-api/docs/integrations/product-price.md)，不能据本地修复推定部署已更新。
参考价格不等于成交价或定损结算结果，观察时间不应改成回答时间。

排障区分四层：数据库连接 → 业务表/列/关联 → 有效现价记录 → 指定型号/规格可匹配。
当前初始化 SELECT 1 不验证后面三层；capability ready 也不是数据覆盖验收。
修复数据需由数据生产者另行执行，不属于 Agent 查询副作用。

## 4. 全品类价格 V2

此前数据检查显示 V2 与 V1 可位于同一 MySQL schema，通过 v2_ 表区分；
这里的“V2 数据库”是逻辑数据模型称呼，不要求另建数据库。
后续只读核对已确认原服务器配置连接的 V2 五品牌和生鲜数据存在，迁移及有限消费样本结果见状态页。
本地示例 DSN 不代表服务器实际配置；不能将占位主机的连接失败解释为原数据库为空。
已有全品类观察包含批发/零售类来源；不能直接把它们映射成手机 SKU。

V2 要区分来源 listing/revision/observation 与规范 brand/catalog_item/item_variant。
已有来源价格不代表规范商品层已经完整，接入前必须复查实际填充和关联；
不要因名称叫 price_current 就认为它与 V1 有相同字段/单位/语义。

后续 read contract 至少需要：

- 类目、品名/品种、规格、地区/市场、来源和可选规范商品关联；
- 金额、币种、计价单位、批发/零售/区间/均价等口径；
- 观察/发布日期、当前或历史查询、来源记录与更新新鲜度；
- 缺值和不可比数据的明确策略，禁止未知单位自动换算、跨口径混价。

目标是统一 V2 只读查询核心，设备与生鲜使用不同匹配策略、共用证据与结果边界，
而不是长期维持 V1 设备和 V2 生鲜两套完整业务。设备使用可信标准商品关联；
生鲜允许仅有来源归一属性。V1 HTTP 可保留薄协议适配，但最终不再读取 V1 价格表。

特别注意：V2 `price_current` 是指向观察的指针；原始报价金额与标准单位价必须分别配对各自单位；
无金额的最新状态不能被上一条有价记录覆盖。生产者原生设备采集不承诺迁移 V1 历史，
所以能力替换和历史数据连续性分开验收。

字段语义与本地冻结边界见[价格消费合同](../apps/assistant-api/docs/integrations/product-price.md)；
切换边界及待确认的生产者缺口维护于[全品类价格实施计划](product-price-implementation-plan.md)。Assistant 仍只读，
不导入生产者实现，不自行建表/修复数据，不在 V2 未命中时自动读 V1。

## 5. RAG 数据与业务可回答性

collection 有记录、召回分数高，都不能证明证据足够回答具体业务问题。
既有公开政策可能支持责任/投诉规则，却不包含某企业理赔材料清单。
需要获准企业指南、适用条件、有效期/地区等资料时先补数据，不让模型从相关条文编造细节。

离线链路和 Milvus 增量操作见[Pipeline](../apps/offline-pipeline/README.md)；
在线字段/拒答见[RAG API](../apps/rag-api/docs/api.md)。更新数据后重验正负例和引用，
不把历史评测数字当成新数据版本质量。

## 6. 数据交接需维护的私有记录

每个来源记录：维护人、连接配置位置/权限、表或 collection/版本、消费字段、
更新机制/最近成功时间、覆盖范围、失败处理、备份与回退点。
缺失“维护人/更新频率”时明确待确认，不凭一次观察推断自动更新机制。

上线前的只读验收应同时检查少量可回答样例和正常无匹配样例。
生产数据、原始 SQL 输出、DSN/Token 和业务回答留在私有审计位置，公共文档只保存脱敏结论。
