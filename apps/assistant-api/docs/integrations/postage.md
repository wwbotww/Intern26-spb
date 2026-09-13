# 资费接口：离线合同、领域规则与外部缺口

[Assistant API](../../README.md) · [项目文档导航](../../../../docs/README.md)

P0～P3 本地 / Mock 通路已完成，真实 P4 未验，按本轮收口约定作为遗留保留、暂不推进。
代码仅接受显式 MockTransport，
不能只填写 URL/Key 就开启生产报价。进度见[当前状态](../../../../docs/current-status.md)。

## 1. 原资料与采用范围

用户资料《资费查询接口.docx》，正文《资费查询接口规范》，原文件 SHA-256：
`d4454efbe1bcaa2c6b28c1be03fa780be629d0d04fdf71a298caa6ff50089f22`。
未见明确版本/生效日，计费章节缺完整业务报文；内嵌 Java login_system 示例不是本业务样例。
原始附件、内部地址和凭据不入仓库。

它是产品/客户/渠道相关的邮件计费试算，不是“两地+重量”的固定价格表。
CSB、流水与分层错误具备企业集成特征；签名字节、响应嵌套、产品/地域、金额与运维语义
缺失，使它还不是可独立互通的完整交付契约。不能仅凭技术名称判断是否符合某家企业规范。

当前实现限定：国内、显式产品、实重、无增值服务的合成试算，不承诺最终支付金额。

## 2. 资料事实与临时协议

服务名 `getAllFeeForInterface`，接口号 3.6.2；CSB params 中
`messageHeader`、`map` 都是 JSON 字符串，不是业务字段直接放顶层 JSON。

| 输入 | 含义 / 限制 |
| --- | --- |
| productCode | 必填；产品目录/资格须明确，模型不能猜码 |
| weight | 必填整数克；1.25 kg 精确转换 1250 g，小数克不擅自取整 |
| senderCityNo / aACode | 寄件地市与寄达行政/国家代码，名称解析成功不等于可计费 |
| custCode / isKD100 | 客户/渠道上下文，不开放给聊天任意指定 |
| height/width/length、duty/insuranceMoney、sendBackType | 体积、保价/保险、回执等；不能把未支持条件静默删掉 |
| isCollectionJudge / isWordOperation 等 | 响应语义未明确，当前不启用 |

profile `postal-postage-synthetic-v1` 固定以下**合成假设**：

1. 只发送四项基础字段，map 按键排序、紧凑、保留非 ASCII、UTF-8 序列化一次。
2. 业务 sign 为 Base64(MD5(raw MD5(UTF8(password + map JSON))))，两次 MD5 都取 raw bytes。
3. messageHeader 含 sysCode/sign/serialNo/sendDate。CSB headers 使用
   _api_name、_api_version、_api_access_key、_api_timestamp（毫秒）、_api_signature。
   公共字段与原始业务参数排序拼接后 HMAC-SHA1/Base64；最后一次 form 编码。
4. 每次实际尝试新 serialNo；Command/tool_call_id 不变，响应必须关联本次流水。
5. 四层依次校验：HTTP → CSB Code=200 → body.retCode=000 → map.errorcode=null。
   CSB 字符串 Code=504 表示 API 不存在，不等于 HTTP 504。
6. retBody 必须是一次解码后含 map 对象的 JSON 字符串；拒绝对象/字符串混用、
   重复键、重复编码、非有限值、缺失 errorcode，不循环猜解码层级。

两层认证独立：CSB AK/SK 不等于业务 sysCode/password，更不能复用轨迹 dataDigest。
外层参考[官方 CSB 示例](https://github.com/aliyun/csb-sdk/blob/master/Java/src/main/java/com/alibaba/csb/CsbSignature.java)，
不证明私有部署兼容。MD5 是遗留兼容，不是加密/安全认证；平台禁用时失败，不绕过。

## 3. 定价前置、确认与恢复

`PostagePreflight` 使用显式产品/地区目录，冻结产品、计费地区、整数克、币种、金额口径、
国内实重范围及非敏感 pricing_scope_ref；profile 与目录内容参与策略指纹。
同名版本内容变化也必须检出，认证材料不进入 State/指纹。

~~~text
补地区/重量/产品 → 冻结 Command → interrupt 展示条件与范围
  → 用户单独确认 → resume 重算指纹 → 一致才执行 → 校验 → 收据 → 报价依据
~~~

提前夹带确认或修改条件时夹带确认不能执行；覆盖冲突与最终报价确认是两步。
改产品/重量、身份语义/配置变化会使旧确认失效；旧暂停查询缺少可信上下文时要求重新发起。
旧完成收据保留历史结果，不伪造新依据、不重新定价；新查询相同条件仍重新取数。

有限关键词仍可能误拒“不需要保价”，也不能覆盖所有自然语言改写；
结构化范围确认不等于完整否定理解。后续专项评测在[路线图](../../../../docs/roadmap.md)。

## 4. 金额、来源与 Failure Handling

- profile 明确 total/standard/customer 口径；不使用 totalFee or realFee or standardFee 兜底。
- 当前合成假设为 CNY 主单位、返回整数克，成功必须有正的 feeWeight；输入重不能代填计费重。
- Decimal 严格验证有限非负金额/精度；null、空串、缺失不补零、不猜币种。
- 非零计泡指标、正的非燃油附加费保守拒绝；已知燃油费可展示但含费关系 unknown，不再相加。
- 地区/产品是请求关联条件，供应商未回显不能声称由其回传确认。
- 公开 result.quote_basis 仅含金额口径/币种/产品、范围、is_estimate、费用及来源；
  不公开内部计价绑定/指纹。资费来源不用轨迹的 history_completeness。
- 旧 UI 快照缺依据显示未知；合成警告在卡片内保留，费用缺失不表示零。

本地无效输入不出网；HTTP 瞬态故障由图最多尝试两次。
CSB/retCode 失败当前非重试；11 类已知计费拒绝保留 postage_quote_* 原因、不变成 no_match，
同时可证明协议健康。错流水、错金额或未知结构是合同失败，不落成功收据。
语义熔断每次记一次，取消释放半开位；不公开 Message/retMsg/errorname。

## 5. 外部缺口台账

以下 **P-G01～P-G12 均未获接口方确认**。责任为项目开发者与接口方共同确认。
已有合成通路不替代这些材料；基础询价不要求先开发所有增值服务。

| ID | 缺口 / 风险 | 当前可执行处理 | 补齐所需证据 / 进入阶段 |
| --- | --- | --- | --- |
| P-G01 | 双 MD5 的 raw / hex、拼接与 JSON 规则不明，可能验签失败 | 固定 password + sorted compact UTF-8 map，raw→raw→Base64；只实现此合成变体 | 接口方独立签名向量，含中文 / 特殊字符 / 空值规则；P4 前 |
| P-G02 | CSB 实际版本、字段位置及私有差异不明 | 公共 CSB 示例的 headers + raw 参数排序 / HMAC-SHA1，毫秒时间戳，无 nonce | 对应部署版本、完整脱敏 HTTP 样例 / SDK 验签结果；P4 前 |
| P-G03 | retBody 是字符串还是对象、是否包含 map 不明 | 一次 JSON 字符串解码 → map 对象；拒绝其他层级、重复编码和重复键 | 成功 / 业务拒绝 / CSB 拒绝完整脱敏样例；P4 前 |
| P-G04 | 可售产品和资格 / 上下架字典缺失 | 复用 synthetic SYN-A / SYN-B 目录，仅离线选择；无默认真实产品 | 至少一个允许产品及客户资格、字典版本 / 更新机制；P4 前 |
| P-G05 | 地市 / 寄达粒度、直辖市 / 国家代码映射不完整 | 使用显式 synthetic 绑定，未绑定地区不执行；resolved 不等于 provider-ready | 国内收寄 / 寄达代码字典、粒度与覆盖范围；P4 前 |
| P-G06 | 币种、元分、total / standard / real 与含费关系未知 | 合成 fixture 选 CNY 主单位 + totalFee；不回退、不补零、不合计燃油或其他费用 | 金额单位、零金额含义、费用组成、有效期与最终实收关系；P4 前 |
| P-G07 | 返回重量 / 计泡单位、公式与缺尺寸语义未知 | 暂定返回整数克，feeWeight 必填；非零计泡指标或正的非燃油附加费停止报价 | 实重 / 计费重 / 泡重单位和适用条件；P4 前 |
| P-G08 | 时区、时间窗口、serialNo 长度 / 重试复用未知 | Asia/Shanghai 为合成配置，UUID 默认 36 字符；每次实际尝试新流水，响应匹配本次流水 | 流水去重 / 重放窗口、时区与时钟偏差要求；P4 前 |
| P-G09 | 网络、HTTPS / 信任链、白名单 / 轮换未提供 | Gateway 限 MockTransport + 固定 .invalid 地址；不读取 .env，不关闭 TLS | 经批准的安全入口 / TLS 信任链 / 凭据流程及单独调用授权；P4 |
| P-G10 | SLA / QPS、业务错误是否可重试、试算副作用未知 | 共享单次超时 / 大小上限；仅 HTTP 明确故障沿用图最多两次，所有 CSB / retCode 失败不自动重试 | 可重试码、限流信号、超时、计费 / 幂等说明；真实启用前重新评审 |
| P-G11 | 认证身份与产品资格 / 计价身份如何关联 | P3 工厂要求 pricing_scope_ref 非敏感主体 / 渠道修订引用，纳入语义指纹；合成身份变化拒绝旧会话确认，密钥不入指纹 | P4 确认真实凭据与计价身份映射、更换何时意味着语义变化；不能跨客户复用上下文 / 收据 |
| P-G12 | 收寄管控 / 话术等只有输入字段，没有输出语义 | 不发送开关，不宣布实现管控 / 自动业务话术 | 对应响应和规则；基础资费后再单独立项 |

关闭条目时记录：证据版本/脱敏位置、日期/确认方、profile 变更、回归、
旧 checkpoint/收据影响及真实请求次数。不能只把 Open 手工改成 Done。
内部 P-I01/02/03/05/06 已按离线范围交付；P-I04 否定理解、P-I08 holdout 转入路线图。
P-I07 的单实例身份/备份/CI/内网发布已完成，其余企业化要求也在路线图单独管理。

## 6. 代码与验证入口

| 边界 | 入口 |
| --- | --- |
| 定价领域/观察 | `domain/postage.py`、`domain/postage_observation.py` |
| 可执行条件 | `services/postage_preflight.py` |
| 协议与网关 | `adapters/postal_postage_contract.py`、`adapters/postal_postage.py` |
| 命令绑定确认 | `workflow/policy.py`、decide/clarify Nodes |
| 离线组合根 | `offline_postage.py` |
| 公开结果 | `api/agent_schemas.py`、Web `PostageResult.vue`、Eval 镜像 |

路径均相对 assistant-api 包，Web 除外。合成签名向量与字节证据见
[fixture manifest](../../tests/fixtures/postal_postage/manifest.json)，不是供应商 golden vector。
`test_phase3bp_*` 覆盖协议、确认、配置漂移、金额/单位、错误、恢复/重放和公开契约。
运行步骤见[本地开发](../local-development.md)，取舍见 ADR
[0014](../../../../docs/adr/0014-explicit-postage-pricing-context.md)、
[0015](../../../../docs/adr/0015-provisional-postage-adapter.md)、
[0016](../../../../docs/adr/0016-command-bound-postage-review.md)。
