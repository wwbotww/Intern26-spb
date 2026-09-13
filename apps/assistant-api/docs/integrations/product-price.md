# 全品类价格 V2 消费合同

[Assistant](../../README.md) · [实施计划与缺口](../../../../docs/product-price-implementation-plan.md) ·
[当前状态](../../../../docs/current-status.md)

合同标识：`product-price-read-2026-09-11`。本页维护字段语义、内部读协议和本地冻结边界，
不是生产者已签署的对外 API，也不证明真实数据库已兼容。
可执行事实定义见 [domain/product_price.py](../../src/spb_assistant_api/domain/product_price.py)，
合成证据及源码快照见 [manifest](../../tests/fixtures/product_price/manifest.json)。

## 1. 依据、范围与未冻结部分

生产者为独立 `device-price-service`。A/B 冻结时审查 HEAD 为
`17ea0a20006939992db72f7460d4bf5395392306`，但相关设备功能仍包含未提交源码；
manifest 用相关文件摘要定位这份审查快照，不能只凭 HEAD 宣称生产版本一致。
当时其 `docs/V2_PHASE_I_BUILD_REPORT.md` 报告阶段 I 本地 MySQL 验证完成，尚未做公司库核对。
后续 D1/D2 工作中已核对生产者 `7191523` 之后的 M 验收文档及目标库增量迁移，
并用当前消费 SQL/行映射做有界真实只读检查；结果和未关闭的 iMac 身份/运行期缺口见状态页。
不反向修改 A manifest 以伪装原夹具来自新采集，也不把有限样本当作全库验收。

| 范围 | 本地冻结内容 | 仍须生产者/环境确认 |
| --- | --- | --- |
| 公共事实 | current 指针、来源版本、金额/单位、质量和地域关联；目标库必需投影已读 | 生产最小权限、实际更新和完整数据覆盖 |
| 设备 | 五品牌官方渠道；规范产品/规格与有效 match；金额或状态分型 | J/K 建档夹具、五品牌扩展配置、运行期状态确认 |
| 生鲜 | 上海零售均价、商务部批发均价；500g / kg；地区与来源日 | 最新真实样例、市场覆盖、更新频率 |
| 无金额状态 | `b72c910e4f31` 的静态语义及消费者校验；目标迁移已核对 | 生产运行期缺失确认/状态推进，不等于静态迁移通过 |
| 恢复与接口 | D 已实现公开契约与 State 4，已限定发布 | 目标浏览器人工交互与正式回滚补验；见协调发布文档 |

本地冻结允许 B 阶段隔离实现；字段变更需修改合同、事实模型和夹具再审查。
不导入生产者 Python 包，不自动迁移业务库，不因 V2 无数据回退旧表。
CI 不需要生产者仓库、业务网络、数据库凭据或模型 Key。

## 2. Repository 必须交付的投影

D1/D2 的自然语言条件和补槽规则另见[商品价格 Understanding](product-price-understanding.md)。
它与下列读取事实不是同一个 DTO；公开结果/State 4 已配套，不能绕过[公开白名单](product-price-public-release.md)。

`ProductPriceReadRecord` 是经过适配的内部只读记录，不是 API 的直接返回值。
它包含完成一致性读取时的 `read_at`、listing 快照、current 指针、观察，以及可选最近有价历史。
以下字段由[固定 SQL/Adapter](../../src/spb_assistant_api/adapters/mysql_product_price.py)提取，
经过[行投影](../../src/spb_assistant_api/adapters/product_price_rows.py)再进入领域校验；装配进度见状态页。

| 内部对象 | 来源与含义 |
| --- | --- |
| `PriceCurrentPointer` | `v2_price_current` 的 listing/revision/observation ID、单一地区和 observed_at；这里没有金额 |
| `PriceListing` | listing.id/current_revision_id/price_nature；revision.id/source_listing_id/quality_status/base_quantity_value/base_unit |
| `PriceSource` | 来源渠道 ID/code/name/type/business_mode；卖家所属渠道、类型/核验状态；来源 URL 取选中 observation 的 crawl.final_url，而非可变 listing.canonical_url |
| `DevicePriceIdentity` | brand、catalog_item、item_variant；来源官方产品/SKU 标识与已归一规格；可信匹配 |
| `FreshPriceIdentity` | revision.normalized_attributes 中的品名/品种/规格/quoted_unit；可选 source_attributes 市场 ID/name；分类由已审核来源与归一 profile 映射 |
| `PricedObservation / AvailabilityObservation` | observation 的 ID、listing/revision/crawl 引用、地域、金额/口径/状态和观察时间 |

固定关联不变量：

1. pointer 指向同一 observation；listing、revision、地区、observed_at 必须相同。
2. revision 属于该 listing，且等于 listing.current_revision_id；revision 与 observation 均 ACCEPTED。
   SQL 同时核验 observation.rejection_code 为空；不能只把质量字段投影为固定字面量。
3. listing.price_nature = observation.price_nature；卖家与 listing 属于同一渠道。
4. 设备 match 必须 ACCEPTED、绑定当前 revision/variant，且在读取时刻有效；区间采用 `[from, to)`，
   无结束表示仍有效。事后建档可晚于观察发生时间，不能据此把合法观察拒掉。
5. SQL/Adapter 仍须核验 variant 属于 item、item 属于对应 brand/category、匹配唯一性、来源白名单与 crawl 引用/数据集关联。
   通过 Python 构造一个合法对象，并不证明这些上游关联真实存在。
6. 使用 current 指针，不用 `MAX(observed_at)` 自建“当前”；同一时点修订依赖生产者 correction 机制。
7. 多条 SQL 使用有界一致性读取；跨用户等待不持有事务。不能拼接旧 revision 金额和新配置。

不要全局过滤 `listing.lifecycle_status=ACTIVE`，否则会丢掉需要展示的下架事实。
`source_channel.enabled` 是采集启用开关，不自动等价于读取发布许可；允许消费的渠道由消费者合同确定。
不选择 raw page、source_hash、私有路径或连接字段用于公开展示。

crawl 校验范围：observation → crawl_record → 同来源渠道的 crawl_run；设备 PRODUCT 的 entity_key
须等于该 listing 的官方产品 ID，允许同产品多规格共享记录；生鲜允许同一公开数据集共享记录。
B 对现有两来源追加本地冻结：`PUBLIC_PRICE:` 后为生产者既定 dataset_key 的 SHA-256，
上海是 `shanghai-fresh-retail:{来源日}`，商务部是 `mofcom-bj:{原生商品ID}:{来源日}`。
来源日取 observation 的 Asia/Shanghai 日期；商务部原生 ID 必须来自 revision.source_attributes
中的 `source_commodity_id`，缺失时拒绝，不从归一 commodity_code 猜测。这个字段仅用于 Adapter 校验，
不加入 Agent/API 的公开身份，不改变 A 的事实夹具。
listing URL、crawl 请求/最终 URL 都要符合固定来源白名单和数据库来源配置的交集；
输出来源使用选中观察的最终证据 URL，避免旧观察配上后来更新的 listing URL。
这些校验不是重新解析 raw 页面，也不能证明页面内容、每个原始价格单元格或生产采集流程全部正确；
追加审查文件摘要在 manifest 中明确标记 B 的局部审查范围。

## 3. 金额、身份与时间语义

### 3.1 报价和无金额状态

内部用 `kind` 区分 `priced` 与 `availability_only`，不以 null 金额猜业务状态。

| 约束 | 规则 |
| --- | --- |
| 金额 | 非空必须严格大于零且有限；现价/原价按 NUMERIC(18,2)，标准单位价按 NUMERIC(20,6)；拒绝 float/bool 和隐式取整 |
| 币种 | 首轮只接受 CNY，保留十进制精度；公开序列化为字符串，不转浮点 |
| 原价 | null 必须配 NONE；非空配 CROSSED_OUT / MSRP / EXPLICIT_ORIGINAL。不自创 LIST_PRICE，也不凭当前价补原价 |
| 直接报价 | RETAIL_OFFER / WHOLESALE_OFFER 必须 DIRECT_UNCONDITIONAL；费用 ITEM_ONLY / SEPARATE_FEES_EXCLUDED |
| 均价 | RETAIL_AVERAGE / WHOLESALE_AVERAGE / MARKET_AVERAGE 必须 PUBLISHED_VALUE、费用 NOT_APPLICABLE；不描述成成交报价 |
| 标准单位价 | 金额与单位封装成同一 `UnitPrice`；UNIT_QUOTED 必填；与原始报价/已知数量矛盾时拒绝，不改写数值 |
| 无金额状态 | 仅 RETAIL_OFFER + AVAILABILITY_ONLY；状态 OFF_SHELF / OUT_OF_STOCK / COMING_SOON；所有金额为空，原价类型 NONE、计价 UNKNOWN、费用 NOT_APPLICABLE、无促销文本 |

无金额状态可以是当前事实，不等于 no_match，也不能显示为 0 元。
带金额的 OFF_SHELF 仍是合法 priced 记录；UNKNOWN 不可推断为在售，抓取失败不可推断为下架。
原价不强加“所有价格性质均须大于当前价”的通用约束；但本次最新审查的生产者价格 policy
已对 RETAIL_OFFER 增加 ORIGINAL_BELOW_CURRENT 门禁。因此首轮官方设备的非空原价不得低于现价，
可等于现价；这不是全库 SQL 约束，也不扩展成所有批发/均价的规则。

### 3.2 设备和生鲜不是同一个 SKU 模型

- 设备范围：PHONE/TABLET/LAPTOP/DESKTOP/WATCH；APPLE/HUAWEI/XIAOMI/OPPO/VIVO，
  对应 `品牌码_CN_WEB` 渠道；OFFICIAL_MALL/SELF_OPERATED、BRAND_OFFICIAL/VERIFIED。
  单件 PIECE、数量 1、NATIONAL/CN；报价为 RETAIL_OFFER/PACKAGE_TOTAL。
- 设备规格保留 `capacity/memory/color/connectivity/size/edition/manufacturer_part_number`，
  扩展配置为具名 attributes，至少一个有证据的维度；价格/库存/标题/URL/时间不充当规格。
  消费边界先做有界结构校验；精确容量归一、扩展维度白名单和匹配策略在 B/C 实施并与 J/K 样例核对。
  官方 SKU ID 不存在则保持空，不能拿数据库 ID 或猜测值伪装成官方编码。
- 设备可有 `CNY_PER_PIECE` 标准单位价，数值应与单件总价相符；不是强制 unit_price=null。
- 上海渠道码为 `SH_FGW_FRESH_RETAIL`，CITY/310100，RETAIL_AVERAGE，PUBLIC_DATA/SELF_OPERATED。
  原始报价是 `CNY_PER_500G`，base_quantity=0.500 KG；标准价是 `CNY_PER_KG`。
- 商务部渠道码为 `MOFCOM_FRESH_WHOLESALE`，逐行 PROVINCE，WHOLESALE_AVERAGE，PUBLIC_DATA/WHOLESALE；
  原始与标准价都按 KG。保留 `XJ_CORPS` 等来源码，不套用六位行政编码或物流 Demo 地区表。
- 两个政府渠道当前均是 PUBLIC_MARKET/VERIFIED，状态 UNKNOWN；市场 ID/name 有证据才成对保留。
  不要求生鲜有品牌、catalog_item 或 variant，不按设备型号解析器理解品名。
  归一商品组保留 VEGETABLE/FRUIT/MEAT_EGG；两来源已审核的 government-fresh profile 对应
  FRESH_MONITORED_COMMODITY。listing/revision 没有可直接读取的 category_code 列，
  Adapter 须核验来源/profile 后映射这个分类，不能凭品名猜字段。
  这两个首轮来源没有营销原价证据，消费者仅接受 original_price=null/NONE；
  这是来源范围限制，不是给所有均价数据或上游 SQL 增加通用原价禁令。
  地区码拒绝 UNKNOWN/MULTI 占位，CN 仅用于 NATIONAL；其余来源码仍需 B 的目录/查询约束，不能据格式校验宣称已覆盖。

例如合成原始价 `3.50 CNY_PER_500G` 与标准价 `7.000000 CNY_PER_KG` 同时存在；
不能输出 `3.50 元/kg`。首轮不支持的计价/来源范围直接拒绝，不以未知单位猜算。

### 3.3 观察、来源日与窄范围历史

- SQL DATETIME 以生产者 UTC 约定读取，Adapter 显式附 UTC；领域层拒绝无时区 datetime，不猜本机时区。
- 政府 `source_attributes.time_precision=DAY` 表示 Asia/Shanghai 来源日零点转 UTC；
  `2026-01-15T16:00:00Z` 表示来源日 1 月 16 日，而非 15 日的价格。
- `read_at` 是该次一致性读取完成时刻，不是排队开始时间；观察不能在此之后。
  `queried_at`、抓取时间、观察时间分开，不能用本次查询时间刷新旧事实。
- freshness 按来源渠道显式配置最大观察年龄，未配置时为 `unknown`；不预置未经确认的业务阈值。
  使用当前观察时间与一致性读取完成时间计算，恰好达到阈值仍为 `fresh`，超过为 `stale`；
  这只表示观察年龄，不保证仍有库存、仍可成交或满足用户的“今天”条件。
  任意历史日期或趋势不在本轮范围，D 的当天/最新需求必须另做语义校验。
  不把 DAY 标签当成精准抓取时刻，也不从问句或回答时刻生成来源日期。
- `last_known_price` 仅可补充无金额当前状态，必须同 listing、同 revision、同地区/币种，且严格早于当前观察。
  本地冻结采取保守的**同 revision**范围；跨 revision 历史必须先证明身份等价，未确认前不返回。
  历史保留自己的 observation/crawl 引用与时间，不能顶替 current 指针。
  当前 SQL 实现保守返回 `last_known_price=null`；领域合同与夹具允许历史，不等于已经交付历史检索。
  后续须证明同一来源时点的 correction 最终节点及历史关联，再补有界读取；见 PRICE-G11。

### 3.4 内部有界读取协议

[查询模型](../../src/spb_assistant_api/domain/product_price_query.py)只描述数据库召回条件，
不是 Agent Command，也不接收自然语言到 SQL 的执行指令。

| 读取类型 | 可用过滤与预算 | 返回语义 |
| --- | --- | --- |
| `DevicePriceReadQuery` | 1～8 个搜索词，每个最多 100 字符；可选品牌/类目；最多 20 个产品，每产品最多 50 条事实 | 先产品、再产品内规格，有固定排序；文本召回不是最终硬身份匹配 |
| `FreshPriceReadQuery` | 同样的搜索词预算；可选商品码、地区、市场、批零口径；最多 100 条 listing/地区事实 | 不依赖标准商品建档；不跨地区或批零口径合并报价 |
| `SelectedPriceReadQuery` | 服务器选择引用中的类目、listing ID 与地区；固定 SQL 最多检查两行 | 只读该身份当前指针，重复行拒绝；不重新模糊搜索、不替换相似商品 |
| `PriceReadBatch` | 同 listing/地区或 observation 不重复；超限使用额外一条探测并标记 `truncated` | `truncated` 表示候选不完整，不声称库中没有其他候选 |
| `ProductPriceQueryResult` | `candidates` / `no_match`，逐事实保留 freshness | 仅返回经过合同验证的召回候选；quote_device / quote_product 再匹配并构造领域结果，API 单独公开投影 |

搜索词去重并转小写，以绑定参数传入；LIKE 的 `%`、`_`、`!` 按字面转义。
文本条件用于宽召回，设备必须继续经过共享硬身份策略；不得将被截断候选直接当成完整最低价范围。

[Query Service](../../src/spb_assistant_api/services/product_price_query.py)借用 Repository，
复验类目、可选过滤与预算，不创建或关闭连接池。Repository 在一次不中断执行中使用
REPEATABLE READ 只读事务；应用组合根负责单一资源生命周期，等待用户期间不持有事务。
取消请求只结束等待，不能声称杀死 DBAPI 线程：并发名额保留至线程实际退出，后续语句检查停止标记，
并以数据库执行与 socket 超时限制在途工作。关闭需等待工作退出后再释放连接池。

readiness 用实际必需投影的 `LIMIT 0` 检查表、列与读取权限，区分 `ready` / `not_ready` / `contract_error`；
不检查生产迁移版本、全表覆盖或 freshness，也不让 `SELECT 1` 代表兼容通过。
加载到候选投影的金额、单位、来源和关联错误必须失败，不能悄悄删行后返回 `no_match`。
完全孤立、无法召回的全库记录不在每次聊天扫描；此类完整性审计属于 E 的独立只读诊断。

### 3.5 设备报价与旧协议投影

`ProductPriceQueryService.quote_device` 返回[内部设备报价结果](../../src/spb_assistant_api/domain/device_price_quote.py)，
不借用 V1 `DevicePriceRecord` 承载 V2。设备解析位于 Domain，
[共享匹配策略](../../src/spb_assistant_api/services/device_price_matching.py)接受泛型事实：
品牌/产品级家族、型号数字、字母数字及已识别的 Pro/Max/Plus/Ultra/SE 后缀先作硬约束，
再作产品排序，最后过滤产品内规格。已识别的型号变体必须一致，不能用高相似度放过额外 Max/Pro。
SKU 标题、容量、内存与尺寸不补进产品身份；同分候选仍保留为候选，不宣称唯一型号或最低价。

`DevicePriceSpecificationFilter` 为内部调用提供显式容量、内存、颜色、连接方式、尺寸、版本、
制造商料号及具名扩展规格条件；容量不能命中内存字段，不存在的属性不能猜测。
2026-09-11 真实旁路发现官方容量保留 `256 GB 1 脚注` 格式：匹配层允许单一容量后紧随
明确数字脚注标记，并仍保留原始证据字符串。范围、多容量、任意前后说明不剥除，
容量与内存不串字段；合成报价和两版隔离 SQL 的精确选择回归覆盖此边界，不修改来源数据。
自然语言已接入 D 的容量/内存/颜色等受支持条件提取与确认，见[Understanding](product-price-understanding.md)；
内部可筛选字段仍不代表每种自然语言表达或任意扩展属性都能提取。

| 内部结果 | 旧设备卡片协议的受控投影 |
| --- | --- |
| `matched`，包含 priced 事实 | 数值证据卡，原价可空，官方 SKU 未提供时旧字段保持空字符串，不用数据库 ID 代替 |
| `matched`，完整结果只有无金额状态 | 内部仍是命中；旧协议用 `no_match` 表达“无数值报价”，reason_code=current_price_unavailable，正文说明已找到的状态、规格、来源与观察时间，价格证据为空 |
| `matched`，priced 与无金额状态混合 | `partial`，只将 priced 投影成数值卡，正文单列无金额规格及其证据时间 |
| 召回不完整，或仅状态子集因展示上限被截断 | 不能据此断言无报价；缺少可呈现的报价时返回 `need_more_info` / price_candidates_incomplete |
| 完整召回仍无型号/规格命中 | 正常 `no_match`，区分型号与规格原因；不会换相近型号报价 |

上表是旧 HTTP/Agent 设备 DTO 的表达限制，不改变 Repository/Service 对“状态命中”的定义。
不能把 `current_price_unavailable` 当成商品不存在，也不能用历史金额、零或空字符串假装价格。
D3 的内部 `product_price` 结果保留金额/状态分型与结果澄清循环；D4～D7 已交付[公开白名单](product-price-public-release.md)，
仍不能把内部读取事实直接序列化给 Web。候选选择/重读见[执行设计](product-price-agent-loop.md)。
来源阈值明确超期时提示非实时；缺阈值保留 `unknown`。召回截断和纯展示截断分别标记，均不承诺全规格覆盖。

### 3.6 受控装配与旧依赖退出

`ASSISTANT_PRICE_DATA_MODEL` 明确选择 `device_v1`（默认）或 `catalog_v2`，二者与 HTTP V1/V2 无关。
选择 catalog_v2 后缺 DSN 就保持能力未配置；缺表、无数据或错误均不自动读 V1。
它使用同一个 `ASSISTANT_MYSQL_DSN`，仅改变消费模型，不迁移数据或扩大账号权限。
V2 产品/规格预算分别用 `ASSISTANT_PRICE_V2_PRODUCT_LIMIT`、`ASSISTANT_PRICE_V2_PER_PRODUCT_LIMIT`，
默认 10/20、硬上限 20/50；展示上限不得超出总候选预算，旧 candidate_limit 只用于 V1。

[配置组合根](../../src/spb_assistant_api/configured_price.py)创建一个 Repository/Service；
应用生命周期初始化 Repository，V1 的 V2DevicePriceTool 包装器和 Agent ProductPriceTool 借用同一 Service，
退出先停消费者再关闭 Repository；catalog 不注册旧 Agent 设备适配器。
启动异常也清理，清理异常不掩盖主异常。代码默认 device_v1 不等于实际发布配置，政策/物流不随之重写。
catalog_v2 已在明确批准的环境限定发布；升级其他持久环境仍须[协调 API/Web、备份与 State 4](product-price-public-release.md)，
**不能只改数据开关让旧客户端直接恢复原 pending**。

C5 的旧依赖清单如下，保留是限定迁移窗口，不是 V2 的运行 fallback：

| 仍保留的旧路径 | 当前调用方与退出条件 |
| --- | --- |
| `adapters/mysql_price.py`、DevicePriceRepository/Record/SearchQuery | 默认 device_v1 装配及其单元测试；E 完成目标覆盖与回退验证后移除生产 SQL 路径 |
| `tools/device_price.py` | 默认旧价格与 agent_demo 的合成设备入口仍保留；身份/排序与 V2 使用同一策略；catalog 另有独立合成入口，不宣称旧 Demo 已迁移 |
| `domain/tools/adapters` 包的旧导出 | 仍服务上述调用方；E 清理时一起处理，不留下隐藏旧查询入口 |
| DevicePriceEvidence/Data 与旧 Agent decoder | 旧协议和历史记录仍需读取，不能跟旧 SQL 一并删除；是否仍保留按 D/E 兼容策略决定 |

Parser 的旧 `tools/device_query.py` 已迁移到 `domain/device_query.py`，调用方全部更新，
不保留第二份解析实现；此前版本可从 Git 历史恢复。

## 4. 设备兼容基线与已知缺陷

[基线夹具](../../tests/fixtures/product_price/legacy_device_baseline.json)包含显式期望，
不是从当前实现自动生成的答案；[基线测试](../../tests/test_product_price_legacy_baseline.py)
让同一组样本分别经过 DevicePriceTool 与 Agent 兼容适配器。

| 已锁定的有效行为 | 验收内容 |
| --- | --- |
| 五品牌身份 | 品牌/家族/型号数字/字母数字、必要后缀；SKU 内存或尺寸不能冒充型号 |
| 规格与候选 | 容量与内存组合、同产品多 SKU、无规格匹配、展示上限 |
| 金额/来源 | 当前价、明确/未知原价、来源 URL 回退、官方标识、UTC 观察时间 |
| 状态/失败 | 有金额的下架/缺货/未知状态；缺型号不查询、正常无匹配、依赖不可用映射 |

两项已确认缺陷在 A 曾单列 strict xfail，**不属于被接受的兼容行为**：

- `device-gap-pro-must-not-substitute-max`：只有 Pro Max 时，Pro 请求可能错误命中。
- `device-gap-base-must-not-aggregate-pro`：共享系列名同分时，基本款结果可能混入 Pro。

C 的共享硬身份策略修复后，以上目标已改为普通通过的回归；原 fixture 的 expected 和摘要不改。
[V2 等价测试](../../tests/test_product_price_device_equivalence.py)让同一组事实经过新包装器与既有 Agent 适配器，
验证行为而不是比较不同抓取日的价格数值。不得通过修改预期为错误结果来“消除失败”。
无金额、完整颜色/尺寸筛选、内存与存储角色的结构化绑定、历史和真实新鲜度不属于旧实现已保证的能力。

## 5. A 阶段冻结的 Agent/API 设计边界

本节保留并更新 A 冻结的公开实施选择。D1～D7 已配套内部循环、公开白名单、Web/Eval 与 State 4；
不能把内部 ReadRecord 直接序列化给浏览器，实际 CI/上线证据见状态页。

| 边界 | 冻结选择 |
| --- | --- |
| 意图 | 新查询统一为 product_price；设备/生鲜条件为有 kind 的联合类型。旧 device_price 仅留历史解码和必要入口兼容 |
| 理解槽位 | 设备品牌/型号/规格；生鲜品名/地区/市场/批零口径；共同的最新/当天约束与期望单位。unknown 类目先澄清 |
| Command | 只有经硬实体和补槽校验的条件可执行；完整原句不替代查询条件；不允许模型提供可信商品 ID |
| 公开价格 | `type=product_price`；身份分型、报价/状态分型、原始单位和标准单位价、地域、来源/观察时间、可选独立历史价 |
| 公开字段白名单 | 允许必要来源/商品描述及证据引用；不暴露内部 listing/revision/observation/variant/merchant ID、参数指纹、私有路径或原始 State |
| 选择输入 | 新增 `price_selection: {candidate_token: string}`；至少 message / explicit_intent / 合法待恢复选择之一，不开放任意 slots 字典 |
| 选择展示 | 在现有 RequiredInput 增加类型化 price 候选项（label/token），不把 JSON 编码塞进 choices 字符串 |
| 候选身份 | 服务器生成不透明高熵 token，关联 owner/query/约束指纹/候选集；到期取生成后 10 分钟与会话 TTL 的较早者 |
| 客户端契约 | 采用 `X-Agent-Contract: product-price-v1`。catalog_v2 浏览器能力目录/会话推进/快照读取在打开 SSE 或返回新类型前检查；缺失/不匹配返回 HTTP 409、既有错误信封及 agent_client_upgrade_required；服务身份/V1 原认证不变 |
| 不受门禁影响 | V1、健康检查和访客身份接口；Agent 元信息入口可提供契约发现。代理/CORS、Web 全部会话请求和 Eval 必须同步 |
| 恢复 | 旧完成 device_price 结果原样解码；旧 pending 无法安全恢复时保留历史，提示新会话，不改指纹或自动执行 |

预算与状态机按[实施计划](../../../../docs/product-price-implementation-plan.md)：2 次逻辑调用、
每 query 1 次技术重试、3 轮澄清；resume 不重置预算。上述接口设计不构成已通过的 HTTP 兼容性证明。

## 6. 验证与交接

从仓库根目录，在[已安装环境](../../../../docs/development.md)中运行：

~~~bash
.venv/bin/pytest apps/assistant-api/tests/test_product_price_contract.py apps/assistant-api/tests/test_product_price_legacy_baseline.py apps/assistant-api/tests/test_product_price_fixture_manifest.py
~~~

- `read_records.json`：四种完全合成的内部读取投影；字段关系负例由合同测试对其独立变异产生。
- `legacy_device_baseline.json`：合成有效行为与单列已知缺陷；保留显式期待，不用未来实现生成 Gold。
- `manifest.json`：绑定夹具、审查源码快照和本地冻结身份；数据变更需重审、更新摘要。
  测试不在运行时读取另一工作树，也不要求其未提交源码一直存在。
- `product_price_device_fixture.py`：将 A 的同一合成证据表达为 V2 事实，不把示例 URL 当作真实 SQL 来源白名单证明。
  `test_product_price_device_equivalence.py` / `test_product_price_device_quote.py` 验报价与旧协议边界，
  `test_product_price_composition.py` 验 HTTP 共用资源、无 fallback 和异常清理。

上面三个 A 阶段测试验证字段和消费者防线，本身不执行 MySQL SQL。
真实 SQL 的合成验收入口、隔离数据库限制和固定 MySQL 镜像见
[MySQL 门禁说明](../../../../deploy/price-query/README.md)；具体运行结果以状态页为准。
它不证明生产 schema、真实覆盖、新 Agent 或线上替换已完成。
缺口在实施计划维护；真实迁移、模型调用、采集与发布分别按授权执行。
