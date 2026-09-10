# 资费扩展缺口台账

> 更新：2026-09-10，P3 公开离线通路、6A-1～6A-3 身份 / 恢复 / 独立部署已本地验证，远程 CI 尚未运行。依据用户要求，将“当前临时处理”与“真实确认”
> 分开记录；不以 TODO 代替可执行路径，也不以合成测试关闭外部合同问题。
> 源证据：[P0 文档评审](agent-kernel-phase3b-postage-analysis.md)；实现：[P1](agent-kernel-phase3b-postage-p1.md)、[P2](agent-kernel-phase3b-postage-p2.md)、[P3](agent-kernel-phase3b-postage-p3.md)。

## 1. 外部合同：保持待确认，离线 P3 不替代互通

下列各项当前状态均为 **Open / 未获接口方确认**。对接责任是“项目开发者 + 接口方”，
不在聊天或 fixture 中索取真实凭据。只有补充相应证据、修订 profile 并回归后才能关闭。

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

## 2. 内部交付状态与残余缺口

| ID | 状态 | 当前边界 | 下一动作与验收 |
| --- | --- | --- | --- |
| P-I01 | Closed / P3 offline | product_code / postage_confirmation 公开，目录选择无默认值；缺产品正常 interrupt | P3 API / Web 与独立 Eval 回归，OpenAPI / TS 已同步；动态产品选项以当前 interrupt 为准 |
| P-I02 | Closed / P3 offline | result.quote_basis 白名单金额依据和独立来源；内部上下文剔除，旧快照未知 | DTO / OpenAPI / Eval 镜像、JSON/SSE / 刷新解析与私有字段负向测试通过 |
| P-I03 | Closed / P3 offline | 独立工厂强制鉴权 / 绝对 SQLite / synthetic profile / MockTransport，忽略环境配置 | owner / readiness / 重建 / 退出测试通过；默认 main 未启用资费，不能把工厂当生产配置 |
| P-I04 | Partial / follow-up | 命令绑定的结构化范围确认、覆盖后重新确认、否定 / 修改 Eval 已完成；“不需要保价”仍保守误拒 | 后续增加范围否定和改写的专门理解样本、明确结构化需求 schema；未经评测不放宽安全门禁或宣称完整理解 |
| P-I05 | Closed / P3 offline | V2 JSON/SSE、刷新恢复、消息重放、新查询取数、失败 Renderer 与浏览器已验收 | Python 922 / Web 55；新增 13 场景 / 28 Turn 公开 Eval、8 次 Mock；执行收据证据复用 P1/P2 |
| P-I06 | Closed / P3 offline | 保持 upstream_unavailable + postage_quote_* 稳定码 + retryable=false；非新增公共枚举 | V2 / Eval 按原因码断言，Web 展示固定业务解释；业务拒绝一次请求，HTTP 超时最多两次；P4 再确认供应商语义 |
| P-I07 | Partial / 6A-1–3 local | [6A-1](agent-kernel-phase6a1-browser-identity.md) 匿名 owner、[6A-2](agent-kernel-phase6a2-sqlite-recovery.md) 整库恢复及 [6A-3](agent-kernel-phase6a3-controlled-deployment.md) 独立 HTTPS / Compose / 冻结镜像 / CI 文件 / 新卷恢复与 V1 回退已本地验证；[工具链收口](agent-kernel-phase6a3-ci-closeout.md)后完整 npm audit 为 0 | 下一步授权后验证远程 CI；Python / OS 扫描、目标环境 / 公网 TLS、个体撤销 / 业务授权、异地加密 / 保留、备份外删除账本及多副本仍待验收，与 P4 / holdout 分开 |
| P-I08 | Deferred / Phase 5 | 代表性模型 holdout 仍待人工审核与另行预算；本轮无模型调用 | 不将 922 个工程测试或合成资费样例转写成意图理解准确率 |

## 3. 已覆盖的工程风险

P2 已通过测试覆盖：签名与传输使用相同字符串、价格不兜底 / 不补零、跨尝试流水检查、
语义与传输失败只计一次熔断、11 种已知业务拒绝不重试、半开取消释放、profile 漂移要求
重新查询、新查询刷新而执行重放不调用。可复跑命令见 P2。

P3 再覆盖命令绑定确认、首次夹带批准 / 改条件夹带批准不执行、计价身份引用变化、
公开依据与来源一致性、费用不求和、旧快照不补币种、未装配能力前置阻断及资源归属。
源码 / 测试 / 浏览器证据和运行入口见 P3；以上 Closed 均仅指明确的离线交付范围。

这些工程风险在**具名临时合同内**已覆盖，不代表 P-G01～P-G12 获得接口方确认。
后续收到材料时，记录：证据版本 / 脱敏位置、确认日期、确认人、变更 profile、相关测试、
是否影响旧 checkpoint / 收据、真实请求次数及结果。不得仅把状态从 Open 手工改为 Done。
