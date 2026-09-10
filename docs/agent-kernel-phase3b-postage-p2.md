# Phase 3B-P / P2：资费协议与离线 Gateway

> 日期：2026-09-09。状态：P2 离线实现完成，新增 160 项测试，Python 全量 887 passed。
> 本阶段不访问真实物流、不调用模型、不读取或修改用户 .env；未新增生产开关。
> 用户要求“缺口先记录，基于已有条件完成通路”，未确认项集中在[缺口台账](agent-kernel-phase3b-postage-gaps.md)。
> 本文记录 P2 完成时的边界；后续 [P3](agent-kernel-phase3b-postage-p3.md) 已完成公开离线集成。
> P4 供应商互通仍暂缓；P1～P3 当前改动尚未提交 / 推送。

承接 [P1](agent-kernel-phase3b-postage-p1.md) 的产品 / 地域 / 重量门禁，新增独立协议
兼容层。复用既有 StateGraph、单次 HTTP Client、语义熔断器、SQLite 和执行收据，
不扩展自由 Agent Loop，不让模型补价。设计决策见 [ADR-0015](adr/0015-provisional-postage-adapter.md)。

## 1. 本阶段打通的路径

~~~text
规则理解 / 产品补槽 → 已绑定 profile 的定价上下文 → PostageTool
  → PostalPostageGateway → 双层签名的 UTF-8 form → MockTransport
  → HTTP / CSB / 业务 / 计费四层检查 → 类型化报价观察
  → Result Validator → SQLite 执行收据 → Graph 回复
~~~

该路径可跨 SQLite 关闭 / 重建恢复；同次执行 checkpoint 重放与消息幂等重放不会
再次请求，相同条件的新查询则重新取数。查询时间是 Adapter 的本地 aware 观察时间，
不是未经确认的报价有效期；历史收据原样重放不代表价格仍有效。

### 1.1 代码职责

| 位置 | 职责 |
| --- | --- |
| [postal_postage_contract.py](../apps/assistant-api/src/spb_assistant_api/adapters/postal_postage_contract.py) | 具名 profile、显式配置、请求 / 分层响应 schema、业务与 CSB signer |
| [postal_postage.py](../apps/assistant-api/src/spb_assistant_api/adapters/postal_postage.py) | Command 映射、单次调用、分层失败、金额投影与语义熔断；独占客户端生命周期 |
| [postage_preflight.py](../apps/assistant-api/src/spb_assistant_api/services/postage_preflight.py) | 接收不透明的 execution profile 指纹，将其绑定到 P1 目录指纹；不导入 Adapter |
| [compose_response.py](../apps/assistant-api/src/spb_assistant_api/workflow/nodes/compose_response.py) | 业务拒绝的固定安全文案，不展示供应商原始消息 |
| [postage_p2_demo.py](../apps/assistant-api/tests/postage_p2_demo.py) | 可复跑的合成烟测，位于 tests 内，不被应用自动导入 |

配置默认 disabled，且此实现**必须显式注入 httpx.MockTransport**，地址固定为
https://postage.invalid/CSB。没有可填写真实 endpoint 的 P2 配置，没有自动网络
transport 或失败后 Fake 回退。enabled=true 在这里仅允许离线模拟，不表示生产开放。
readiness 也只描述本地启用 / 关闭 / 熔断状态，不探测供应商。

## 2. 冻结的临时合同：postal-postage-synthetic-v1

这些是可测试的工程选择，不是从空白业务报文样例中“确认”出来的合同。
变更选择必须更新 profile 身份 / 测试和台账，不得增加自动猜测分支。

### 2.1 请求和签名

1. P1 已冻结的四项映射：产品 → productCode，整数克 → weight，寄件计费绑定 →
   senderCityNo，寄达计费绑定 → aACode。全部为字符串，未使用的可选字段不发送。
2. map 按键排序、无额外空白、保留非 ASCII、UTF-8 序列化一次；业务签名暂定为
   Base64(MD5(raw MD5(UTF8(password + map JSON))))，两次 MD5 都取 raw bytes。
3. messageHeader 包含 sysCode、sign、新的 serialNo 和显式时区格式化的 sendDate，
   同样按固定方式序列化。map 与 messageHeader 这两个原字符串参与外层签名并原样交给 form。
4. CSB 的公共字段放在 headers：_api_name、_api_version、_api_access_key、
   _api_timestamp（epoch 毫秒）、_api_signature。公共字段与未 URL 编码的业务参数按
   名称排序，name=value 以 & 拼接后 HMAC-SHA1 / Base64。无重复字段、数组参数或 nonce。
5. HTTPX 最后只对 form 编码一次，不预编码 JSON，不把 AK 放入 URL，不发送 SK/password。
   每次实际尝试重新产生流水号；图内 command / tool_call_id 保持不变。

外层算法 / 字段 / header 位置参考阿里云官方仓库的
[CSB Java 签名示例](https://github.com/aliyun/csb-sdk/blob/master/Java/src/main/java/com/alibaba/csb/CsbSignature.java)，
form 和编码顺序参考
[HTTP SDK README](https://github.com/aliyun/csb-sdk/blob/master/http-client/README.md)。
查阅日期为 2026-09-09；公开示例不证明私有部署版本兼容。没有安装或执行 Java SDK。
旧算法仅封装为兼容边界，不视为现代加密；平台禁止 MD5 时安全失败，不绕过限制。

### 2.2 响应、金额和范围

- 外层必须为对象且 Code 是字符串；CSB 成功后才检查 body。
- body.retCode 为字符串，serialNo 必须与本次尝试一致，retDate 必须是合法日期时间文本。
  retDate 不被投影为报价有效期，也不替代本地观察时间。
- 成功的 retBody **只允许一个 JSON 字符串**，一次解码后为含 map 对象的对象。
  拒绝重复键、非有限常量、对象 / 字符串混用和重复编码；不递归尝试不同嵌套。
- map.errorcode 必须显式存在；null 是成功，无错误消息。空字符串、缺失、“0000”
  不被推定为成功。错误码优先于任何同时返回的价格。
- 选定金额字段只能由 profile / catalog 的 total / standard / customer 决定；
  默认 fixture 明确选 totalFee。不存在 totalFee or realFee or standardFee 兜底。
  已提供的其他已知金额字段也做检查；null 不变成 0，空串 / 浮点 / 科学计数 / 超两位小数拒绝。
- 当前合成合同暂定金额为 CNY 主单位，返回称重和 feeWeight 为整数克；成功必须具备
  正的 feeWeight，返回称重须与已发送整数克一致，不以输入重量代填计费重量。
- volWeight / volRatio 仅接受缺失、null、空或数值零的字符串；非零 / 非法值停止报价。
  正的非燃油附加费也保守拒绝，避免将额外服务当作基础询价。已知燃油费可以展示在内部
  明细，包含关系仍为 unknown，**不与报价金额再相加**；明细缺失不代表所有费用为零。
- 地区 / 产品是本地请求关联条件。文档未定义对应的响应回显字段，不宣称供应商回传
  验证了这些条件。未知原始字段和 Message / retMsg / errorname 不进入报价或收据。

报价来源恒为 fake_gateway / synthetic-postal-postage，保留“合成演示，不可用于实际
寄递”的警告。直接构造 production 来源不是本阶段提供的功能。

### 2.3 版本与恢复

profile 的非敏感语义内容生成 SHA-256，再与目录内容共同生成 pricing policy 指纹；
在参数指纹生成前即完成绑定。同名 profile 修改 API 版本、时区或金额口径也会影响指纹。
Gateway 构造时核对 preflight 绑定，调用前重验 command，上下文变更后旧暂停查询要求重启。

未绑定 profile 的 P1 指纹算法保持不变；旧 Fake Command 的 null 新字段仍不影响历史
参数指纹，没有新增 SQLite 表或数据迁移。已完成收据保留历史报价，不重签 / 重新解释。
密钥不进入上下文指纹；同身份密钥轮换不造成价格语义变化的假设，及身份变更如何绑定
报价资格，另见台账 P-G11，不能据此把不同客户的凭据当成可互换。

## 3. Failure Handling 与重试边界

| 情况 | 对用户 / Graph 的行为 | 熔断记账 |
| --- | --- | --- |
| 本地命令、配置、时钟、签名不可用 | 调用前失败，不自动改签名 | 不记上游失败 |
| HTTP timeout / 429 / 5xx / 连接失败 | 沿用传输层明确类别；仅图执行有界重试，默认最多两次尝试 | 每次模拟调用记一次失败 |
| CSB 非 200 / body.retCode 非 000 | 固定安全原因，当前均非重试，待私有协议确认 | 技术失败一次 |
| 文档列出的 11 种计费拒绝码 | 无报价、非 no_match、非重试；提示手工询价 / 资格 / 无费区等实际原因 | 完整协议已到达计费拒绝，视为健康，可关闭半开状态 |
| 未知计费码、错流水、缺金额、错误嵌套、计泡或额外服务响应 | 契约失败，停止展示，不落成功执行收据 | 技术 / 语义失败一次 |
| 协程取消或本地观察时钟错误 | 释放半开探测占用，不伪造响应 | 不改变健康计数 |

CSB Code="504" 表示 API 不存在，不等于 HTTP 504 超时；对应测试明确区分。
未新增公共 FailureCategory：业务拒绝暂借 upstream_unavailable + 固定 postage_quote_*
code + retryable=false 表达，使用独立的适配器内部拒绝类型决定健康记账。后续是否需要
公开业务拒绝类别，由 P3 按 API / Web / Eval 一起设计，不能让粗类别掩盖业务原因。

## 4. 复现与验收

在仓库根运行：

~~~bash
LANGGRAPH_STRICT_MSGPACK=true .venv/bin/pytest -o addopts='' -q \
  apps/assistant-api/tests/test_phase3bp_postal_postage.py \
  apps/assistant-api/tests/test_phase3bp_postage_circuit.py \
  apps/assistant-api/tests/test_phase3bp_postage_adapter_workflow.py
# 160 passed

LANGGRAPH_STRICT_MSGPACK=true .venv/bin/pytest -o addopts='' -q
# 887 passed（P1 基线 727）

cd apps/assistant-api
LANGGRAPH_STRICT_MSGPACK=true ../../.venv/bin/python -m tests.postage_p2_demo
~~~

烟测输出：补产品、SQLite 重启、签名请求、四层检查、收据零调用重放、新查询均通过；
mock_requests=2，live_requests=0。SQLite 使用临时目录，退出后清理，不修改现有会话库。
示例 12.30 CNY / SYN-A 只是 fixture 数字，不可用于实际寄递。

签名向量位于 [fixture 目录](../apps/assistant-api/tests/fixtures/postal_postage/manifest.json)，
使用本地 OpenSSL 独立复算并冻结，包含中文、+&=、百分号、转义和空值的字节测试；
不是供应商 golden vector。业务 Gateway 的 MVP 请求仍仅允许四个经过校验的字段。
160 项覆盖配置 / 传输门禁、分层响应、金额 / 重量 / 流水、14 类技术失败单次记账、
11 种业务拒绝、大小 / 总超时 / 取消、配置漂移、两次尝试预算和 SQLite / 消息 / 收据重放。

Web 本轮未改代码 / 公开 DTO；既有 29 项测试、生成类型检查与默认生产构建通过。
没有将本次 smoke 写成新版资费 Web、V2 或独立 Eval 场景验收。

## 5. 下一步 P3

以下为 P2 完成时的交接顺序，现已在 [P3](agent-kernel-phase3b-postage-p3.md) 按离线范围完成。

按台账先完成无需外部资料的内部通路：公开产品槽位及受控离线装配 → 白名单报价依据 /
来源 → Web 明确合成提示、产品 / 重量修改与确认 → Eval 多轮场景与恢复。
必须同步 OpenAPI / TS / 前端运行时校验 / Eval 镜像；没有资费装配的默认服务保持 unavailable。
外部合同问题继续记录，不阻塞合成集成，也不提前开放真实 transport。
