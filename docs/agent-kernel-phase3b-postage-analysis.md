# Phase 3B-P：资费接口评审与后续切片建议

> 日期：2026-09-09。P0 文档分析完成；随后 [P1 领域 / 可执行条件](agent-kernel-phase3b-postage-p1.md)
> 已完成本地实现与 86 项新增测试；[P2](agent-kernel-phase3b-postage-p2.md) 又完成协议离线通路。
> 本文保留初始评审依据；[P3](agent-kernel-phase3b-postage-p3.md) 已完成公开离线集成，P4 仍待确认，合同缺口见[台账](agent-kernel-phase3b-postage-gaps.md)。
> 用户确认各接口暂时无法真实访问；本次不探测物流接口、不索取凭据、不调用模型。
> 轨迹 T0～T3 已按本地 / Mock 范围收尾，T4 真实联调暂缓；不记为整个 Phase 3B 完成。

## 1. 结论与证据范围

这是一份**邮件计费试算接口**文档，不是按两地和重量查一张固定价格表。它要求指定
产品，且支持客户价、体积计费、保价 / 保险、回执及渠道优惠等计价条件。
足以开展领域差距分析和 provisional 离线切片，但还不足以保证签名互通或金额解释正确。
当前可以规划“国内、明确产品、按实重、无增值服务的基础询价”，不能据此承诺任意产品
的最终支付金额，也不能把未知条件静默当成无影响。

证据来自用户提供的《资费查询接口.docx》（正文标题《资费查询接口规范》），已读取
正文 / 表格并查看内嵌图片。原文件 SHA-256：

```text
d4454efbe1bcaa2c6b28c1be03fa780be629d0d04fdf71a298caa6ff50089f22
```

正文未见明确发布版本 / 生效日期；通用样例的 2017 日期不是文档版本。第 5 节为 CSB
通用接入，第 6 节为计算邮件资费，第 6.1.3 / 6.1.4 分别为应答参数 / 报文样例。
目录仍写“预处理接口”，正文写“计算邮件资费”，章节编号也未统一。
内嵌 Java 图片是通用 `login_system` 调用示例，**不是本业务的完整请求 / 响应样例**；
6.1.4 中只有“请求报文 / 返回报文”标题，没有具体内容。

本文不复制原始附件、凭证、内部地址或组织身份进仓库。文档中的调用建议作为被评审
的协议资料，不是启用服务、安装 SDK 或发送请求的授权。

## 2. 文档已明确的接口事实

### 2.1 接入方式：CSB 外层 + 邮政业务消息头

接口编号 `3.6.2`，API 服务名 `getAllFeeForInterface`，文档描述 HTTP 调用，地址“见凭证信息”。
通用说明中的服务版本默认为 `1.0.0`；这不证明实际测试环境也使用该版本。

请求交给 CSB SDK 的 `params` 是 `Map<String, String>`，本接口的两个参数是：

- `messageHeader`：业务消息头的 JSON 字符串；
- `map`：计费条件的 JSON 字符串。不是把全部业务字段直接放在顶层 JSON body。

存在两套独立认证材料：

| 层级 | 材料 / 规则 | 项目处理原则 |
| --- | --- | --- |
| CSB 接入层 | AK / SK，SDK 生成网关签名 | 独立配置与版本化封装；不与业务 password 混用 |
| 业务消息头 | `sysCode`、`password`，`sign = base64(md5(md5(password + 其他入参)))` | 原文只有公式，字节细节仍需确认；不复用轨迹 `dataDigest` |

消息头还要求唯一 `serialNo`（请求长度 50，响应表写 36）和 `sendDate`
（`yyyy-MM-dd HH24:mm:ss`，未给出时区）。UUID 字符串可满足两侧长度，但重试时是否
必须使用新流水号、服务端是否去重，文档没有规定。

公开 [阿里云 CSB SDK README](https://github.com/aliyun/csb-sdk/blob/master/http-client/README.md)
说明了 AK/SK 签名、参数排序及 HMAC-SHA1 / Base64，并将旧 `HttpCaller.doPost` 方式
标为不推荐、推荐 Builder。这只能作为外层协议参考，不能证明私有云实例的版本兼容，
更不能补齐邮政业务的双 MD5 签名规则。

### 2.2 计费输入

以下字段位于 `map` 内；数字在 wire 上也是字符串。

| 字段 | 原文要求 / 含义 | 对当前需求的影响 |
| --- | --- | --- |
| `productCode` | 必填，可售卖产品 Code | 现有流程必须增加产品选择或经确认的显式默认产品；不能由模型猜代码 |
| `weight` | 必填，整数，克 | `kg/g` 可做精确单位转换；小于一克的精度和上限未定义 |
| `senderCityNo` | 必填，收寄地地市行政区划 | 需要可用的寄件地市编码，不只是地名解析成功 |
| `aACode` | 必填，寄达地行政区划代码 / 寄达国家代码 | 国内粒度和国际代码表未给；不能自动拿任意 county/city code 顶替 |
| `custCode` | 可选，客户代号 | 可能影响实收价 / 产品资格，应来自经过授权的业务配置，不从聊天任意注入 |
| `height` / `width` / `length` | 可选，厘米，用于计算体积 | 可选不等于所有产品均不需要；用户说“大箱子”时不能假装是完整按实重报价 |
| `duty` / `insuranceMoney` | 责任类型 / 保额，后者最多两位小数 | 保价和保险是不同条件；仅有保额不能确定服务类型 |
| `sendBackType` | 可选，`2` 为回执 | 需要独立服务选择，不能由通用“查询资费”默认打开 |
| `isKD100` | `1` 为指定到站寄件渠道优惠，其他为否 | 业务渠道配置，不是前端任意可选的降价开关 |
| `isCollectionJudge` | `1` 计算收寄管控 | 没有对应响应字段 / 行为说明，不能据此宣布邮件可收寄 |
| `isWordOperation` / `wordOperationType` | 可请求话术；开启时需类型，含 `4` 文本机器人 | 应答表没有话术字段，第一切片不依赖它生成回答 |

### 2.3 输出与成功判定

至少需要区分四层：`HTTP 状态 → CSB Code → body.retCode → 计费 map.errorcode`。
传输成功不等于报价成功；CSB JSON 中的 `Code="504"` 是“服务 API 不存在”，
**不是 HTTP 504 网关超时**。

- CSB 层：`Code="200"` 表示访问处理成功，另有 `RequestId`、`Message`。
- 业务层：`body.serialNo`、`retCode`、`retMsg`、`retDate`、`retBody`；
  `retCode="000"` 为成功，`001/002` 授权 / 越权，`010/020/099` 为业务 / 系统 / 其他异常。
- 计费层：无错误时 `errorcode` / `errorname` 为 null；有错误时列出 `0001` 零计费重量、
  `0002` 产品错误、`0003` 手工询价、`0005/0006` 客户资格限制、`0008/0009` 无计费区 / 标准资费等。

通用部分把 `retBody` 声明为长度 200 的 String，业务部分又说 `map` 是对象节点、
String / `Map<String,String>`。**`retBody` 是否需要一次 JSON 解码、其中是否再包一层 map，
缺少样例不能唯一确定**。未来只接受选定 profile 的明确层级，不做“不断 json.loads 直到能用”。

| 返回字段 | 已明确语义 | 仍未明确 |
| --- | --- | --- |
| `weight` / `feeWeight` | 输入称重 / 计费重量；计费成功必返后者 | 返回单位、精度及边界值；不能仅从输入为克推定全部重量字段单位 |
| `volWeight` / `volRatio` | 计泡重量 / 系数，空或 0 代表未满足计泡条件 | 计算公式、单位、适用产品及尺寸缺失语义 |
| `totalFee` | 总资费，最多两位小数 | 币种 / 元分单位，以及是否含挂号、全部增值服务 |
| `standardFee` / `realFee` | 标准资费 / 实收客户资费，最多两位小数 | 与 totalFee 的关系，客户缺失时如何选取 |
| `discRate` | 百分比整数 | 取整规则与适用金额范围；不能据此反算一个缺失的价格 |
| `startWeight` / `nextWeight` | 起重 / 续重 | 单位；没有完整续重单价，不能本地重建计价引擎 |
| `regFee` | 国际小包挂号费，不含在 standardFee / realFee 内，需归集 | 是否已含在 totalFee，不能再无条件相加 |
| 增值服务费用 | `BXF` 保险、`BJF` 保价、`YGF` 验关、`BGF` 报关、`FJF` 燃油、`HZF` 回执、`MMTDF` 密码投递、`DYFWF` 打印、`BJSXZF` 保价手续费；无相关费用为 null | 汇总关系、与产品 / 服务选择的条件约束 |

未知金额、缺字段或 null 不能转成 0；不能采用 `totalFee or realFee or standardFee`
作为默认显示策略。所有金额将用 Decimal 严格解析有限非负数，并依据确认合同检查精度，
但不预先发明费用合计公式。币种 / 金额单位确认前，不发布“人民币最终支付价”。

## 3. 对技术使用与规范化的评价

它具有企业系统集成的特征：统一服务总线、调用身份、流水关联、分层错误码和较丰富的
业务字段；使用 Java / CSB 本身不能说明技术落后，也不能仅凭一份接口文档判断组织
是否符合某家大厂的全部内部要求。

作为**可供团队独立接入的交付契约**，主要短板是可执行性和语义完整性：

| 缺口 | 风险 | 进入真实联调前所需证据 |
| --- | --- | --- |
| 业务签名只有公式 | 首轮 MD5 用 raw bytes 还是 hex、第二轮输出和 Base64 输入不明；JSON 顺序 / 空白 / 转义影响签名 | 含非 ASCII、特殊字符及空值规则的签名向量；明确 password 与 map 拼接规则 |
| CSB SDK / 私有部署版本不明 | 外层公共字段、签名位置、编码和错误处理可能不兼容 | 确认 SDK / 协议版本、Content-Type 和完整脱敏 HTTP 样例 |
| 响应结构不唯一，业务样例为空 | 成功与失败无法形成独立可复算 fixture | 完整成功 / 业务拒绝 / CSB 拒绝样例，明确每层 string 与 object |
| 产品 / 地域字典缺失 | 地名识别成功仍不满足接口；代码错会得到错误产品报价 | 产品资格 / 上下架、国内地域粒度、直辖市及国际代码字典版本 |
| 金额及重量语义不完整 | 展示错误币种、重复累计费用或把计泡报价当实重价 | 币种 / 元分、重量单位、计泡条件、含费关系及零金额语义 |
| HTTP 与签名被当作完整安全说明 | 签名不能替代传输保密；时区 / 重放窗口 / 密钥生命周期不明确 | HTTPS 或批准的受保护传输边界、TLS 信任链、轮换与白名单、重放策略 |
| 运维契约缺失 | 无法合理设置调用预算和重试 | QPS、超时、SLA、幂等、限流信号、可重试码及维护策略 |
| 话术 / 收寄管控只有入参 | 无法验证对应能力是否返回、如何解释 | 响应字段和业务规则；第一版暂不依赖 |

双 MD5 加 Base64 不是加密，也不是标准 HMAC；多算一次不能视为已满足现代安全要求。
[RFC 6151](https://www.rfc-editor.org/rfc/rfc6151.html) 说明了 MD5 的安全限制。
项目应将遗留兼容算法封装在明确 profile 中，并要求受保护传输；不能擅改成 SHA-256
后声称仍兼容，也不能为联调关闭 TLS 验证。文档写 HTTP 不足以证明实际部署一定是明文，
需要接口方确认。

## 4. 与当前项目的契合点和缺口

以下为 P0 评审时的代码检查事实。P1 已补齐部分差距，当前实现和剩余限制见
[P1 说明](agent-kernel-phase3b-postage-p1.md)；这里是 P0/P1 时的分析边界，后续 P2/P3 各自单独验收。

| 当前模块 | 可复用的能力 | 需要补齐的边界 |
| --- | --- | --- |
| [commands / slots](../apps/assistant-api/src/spb_assistant_api/domain/commands.py) | `PostageCommand` 有起止地区、WeightValue、可选 product_code / declared_value；Slots 有可选 product_code | product 当前非必填，declared_value 尚无完整补槽路径；`resolved` 地区不保证带可用 provider code |
| [Understanding](../apps/assistant-api/src/spb_assistant_api/services/query_understanding.py) / [SlotMerger](../apps/assistant-api/src/spb_assistant_api/services/slot_merger.py) | 资费意图、重量提取、地区澄清、跨轮冲突确认 | 未实现产品目录解析；missing slots 当前只要求 origin / destination / weight |
| [RegionResolver](../apps/assistant-api/src/spb_assistant_api/services/region_resolver.py) | 可注入、带版本的目录与地名歧义处理 | 内置仅 8 条 demo 地区，不能冒充全国或供应商正式代码表 |
| [PostageTool](../apps/assistant-api/src/spb_assistant_api/tools/postage.py) / Gateway Port | `quote(command)` 类型边界、固定结果文案、无本地造价 | 只有 Fake Gateway；当前 Gateway 返回 `PostageData 或 None`，不足以完整表达多层失败与报价观察信息 |
| [PostageData](../apps/assistant-api/src/spb_assistant_api/domain/results.py) / [Validator](../apps/assistant-api/src/spb_assistant_api/services/result_validator.py) | Decimal 金额、输入 / 计费重量、产品、币种、aware 查询时间、路线 / 输入重量一致性 | 无金额口径、费用明细 / 报价条件；成功当前不强制 billable_weight，不校验产品匹配或来源语义 |
| HTTP / Workflow | 单次传输、超时 / 大小上限、Graph 重试预算、查询作用域、收据、SQLite、Trace | 重用基础设施与模式，不复用轨迹字段、签名、错误映射或熔断业务判定 |
| [受控组合根](../apps/assistant-api/src/spb_assistant_api/configured_agent.py) / V2 / [Web](../apps/chat-web/src/components/results/PostageResult.vue) | 默认关闭、生命周期、可用性前置检查，已有资费结果卡片 | 尚未注册真实资费 Gateway；公开 provenance 当前只支持轨迹投影，资费卡片无金额依据 / 来源展示 |

### 4.1 推荐的职责分配（Proposed）

- **Query Understanding**：继续用现有规则 + 可选模型 fallback 判断资费意图、提取用户
  条件；产品名称由经审核目录映射，代码 / 单位 / 权限由确定性规则验证。模型不生成
  `custCode`、AK/SK、业务签名、行政代码或缺失价格。
- **Routing / Stateful Workflow**：沿用 postage 分支。能力不可用先结束；可用时逐轮补
  地区 / 重量 / 产品，对修改确认覆盖，再生成可执行命令。产品目录未配置不等于“请用户
  多给一个猜测代码”；无法执行时停止收集。显式要求保价、国际件或计泡而第一版不支持，
  就澄清 / 提示范围，不悄悄删掉条件生成另一种报价。
- **定价前置校验**：区别地名 resolved 与 provider-ready。单产品模式也必须显式配置
  已确认产品，并对用户显示假设；如果多产品，先做确定性选择。声明价值不是默认购买保价。
- **Adapter / 兼容层**：新增独立资费 wire contract、业务 signer、CSB signer / caller 和
  PostalPostageGateway。共享 HTTP client 已有 form / headers / params 能力；签名位置
  以确认的 profile 为准。若只能使用私有 Java SDK，再评审窄化桥接服务及额外部署成本，
  不让 Java SDK 或供应商字段进入 LangGraph Node / Domain。
- **Agent Loop / Failure Handling**：图仍是唯一重试预算所有者；SDK 自动重试必须关闭或
  可计数控制。优先复用 2 次尝试的上限，不把它当供应商 SLA，也不在多个产品间无界试价。
- **结果契约**：建议增加供应商中立的报价观察封装，记录 source / profile / aware
  queried_at，并扩展金额依据、已知费用项和报价条件。保留用户原始输入重量，与发送
  的精确整数克分别校验；返回 feeWeight 不能拿 input_weight 补齐。记录请求关联条件，
  不假称供应商回传验证了其未返回的路线 / 产品。
- **API / Web / Eval**：同步扩展公开来源、生成类型、运行时校验和旧快照兼容；资费
  不使用轨迹 history_completeness 解释价格准确度。报价应显示适用产品、条件、来源和
  查询时间，以及“试算而非最终支付价”。不要保留未经验证的 CNY UI 兜底作为正式币种。

产品 / 计费上下文必须在生成执行参数指纹前确定，并保存非敏感版本信息。用户发起
相同条件的新查询仍重新询价；同次执行的已完成收据重放保留原报价时间。重启后配置变化
不能让一条恢复中的查询悄悄换产品 / 金额口径，旧状态缺少新增必需条件时要补槽或安全
要求重新查询。query_id / tool_call_id 与 CSB 每次尝试的 serialNo 分工不同；客户端收据
不能证明上游只执行或只计费一次。

### 4.2 失败策略（待用真实样例冻结）

| 情况 | 预期处理 |
| --- | --- |
| 零 / 非法重量、无产品、地区代码不足 | 网络调用前校验 / 补槽；不计上游熔断 |
| 请求含未支持的国际 / 体积 / 增值条件 | 明确能力范围并澄清或结束，不以缩减条件的报价冒充满足请求 |
| CSB 授权 / 签名 / API 不存在，业务 retCode 001/002 | 安全的非重试失败；保持调用方服务鉴权与供应商鉴权分离，不能让用户提供运营凭据 |
| 0003 手工询价、0005/0006 产品资格、0008/0009 无费区 / 标资 | 识别为业务不可报价，不重试；不能统统转成“无匹配”或“地区不支持”，更不能让模型估价 |
| HTTP 超时 / 429 / 5xx | 在确认只读与幂等边界后由图执行有界退避；不把 CSB 同号业务码当 HTTP 状态 |
| CSB 500/801、retCode 020、未知 800 / 099 等 | 分层保留安全原因码；没有确定瞬态语义的业务码先非重试失败 |
| HTTP 200 但 JSON / 嵌套 / 金额 / 流水关联不合法 | 契约失败，无可用报价；不尝试多个签名或响应变体 |
| 已确认格式的业务拒绝 | 与技术不可用分开；不因用户选错产品就熔断全部资费查询 |

现有 FailureCategory 没有独立供应商鉴权类别；可先用 `upstream_unavailable` + 固定安全
code + `retryable=false`，是否增加业务不可报价类别应在契约阶段明确，避免随意扩枚举。
未来语义熔断在 Gateway 统一记账，完整契约校验后确认健康；不双重计数，不直接展示
`retMsg/errorname/Message`，也不在 Trace 中记录原始 map、客户代号、地址或金额条件。

## 5. 后续开发顺序与验收门槛

P0 分析之后已完成 P1～P3，本地测试通过；下一独立切片为 6A。网络不可达不阻止合成离线实现，但不能
通过离线测试消除业务合同歧义。各假设必须具名、可替换，并与供应商确认记录分开。

| 切片 | 内容 | 退出条件 / 当前状态 |
| --- | --- | --- |
| P0：文档评审 | 契约事实、差距、确认清单、MVP 与模块边界 | 本文已完成；不是 profile / 金额口径已确认 |
| P1：领域与可执行条件 | 产品 / 地域目录边界、整数克校验、MVP 限制、报价数据 / 观察封装、补槽和兼容处理；冻结 synthetic 条件 | 已完成本地切片，86 项新增测试，全量 727；保守范围规则并非完整语义理解，详见 P1 说明 |
| P2：协议与离线 Gateway | 明确 provisional 嵌套和签名 profile、请求一次序列化、两层签名、分层错误、语义熔断、MockTransport | 本地完成；160 项新增后全量 887，通过字节 / 编码 / 畸形响应 / 超时 / 取消 / 业务拒绝和 SQLite 通路；本地向量不是供应商 golden vector |
| P3：受控 V2 / Web / Eval | 默认关闭的资费装配，来源和金额口径展示，多轮改产品 / 改重量、恢复 / 重放、回归场景 | 本地离线完成；新增 35 Python / 26 Web，全量 922 / 55，公开 Eval 13 场景 / 28 Turn；真实网络关闭 |
| P4：合同确认与真实联调 | 网络可达后确认两层协议、代码字典及金额口径，修订 profile，取得凭据和单独调用授权 | 暂缓；必须保留可计数真实请求 / 脱敏证据，之后才称供应商互通 |

P1 / P2 至少覆盖：`1.25 kg → 1250 g`、小数克拒绝而不擅自取整、0 / 负数 / NaN / Infinity、
地区只有名称无 provider code、产品缺失 / 冲突、超过本地安全上限、中文及 `+&=` 编码、
map 序列化一致性、各层错误码冲突、null / 缺失 / 空字符串的区别、成功缺 feeWeight、
金额未知或超精度、serialNo 不匹配、新查询刷新与完成收据重放。体积条件尚未支持时的
明确拒绝也必须进入多轮回归，而不是只测“北京到上海 1 kg”的成功路径。

若暂不做资费离线切片，可继续 [主计划](agent-workflow-implementation-plan.md) 的 6A：
代理身份、单实例卷 / 备份、CI、Compose 和回退。它与真实物流联调、独立模型 holdout
各自验收，不能互相代替。

## 6. 留给接口方的确认材料

网络恢复前无需提供秘密材料；后续优先要**非敏感合同证据**：

1. 完整脱敏的成功 / 业务拒绝 / CSB 拒绝 HTTP 样例，明确双层 JSON 的真实结构和类型。
2. 两层协议版本、业务签名的独立向量、序列化 / 空值 / 编码顺序、时间与流水号规则。
3. 至少一个允许的产品及适用客户类型，寄件地市 / 寄达地区编码与字典版本。
4. 金额币种 / 单位、总资费组成、实收与标资关系、挂号与增值费用是否已包含。
5. 返回重量单位 / 计泡规则、无尺寸时的含义、试算有效性和下单实收的区别。
6. 只读副作用 / 幂等、超时 / QPS / 重试规则，以及安全接入和授权测试范围。

接口可达且确认范围后，再按本地受控配置流程提供凭据；不在聊天、Git、fixture 或
浏览器环境变量里传递 AK/SK、password，也不复用轨迹凭据。
