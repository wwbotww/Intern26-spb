# Phase 3B-P / P3：资费公开契约、确认流程与离线 Web 闭环

> 2026-09-09：本地离线切片完成。P3 新增 35 项 Python、26 项 Web 测试；
> P3 收尾时完整 Python 922 passed，Web 55 passed。独立 V2 Eval 为 13 场景 / 28 Turn，
> 全部通过，8 次 Mock 请求；真实物流 / 模型调用均为 0。不是供应商互通或理解准确率。

## 1. 已交付范围

- 严格资费 Descriptor 可注入公开 V2；公开 product_code 与 postage_confirmation 输入。
- 产品下拉选择无默认值。补齐条件后展示地区 / 产品 / 整数克 / 国内实重无增值服务范围，
  独立确认后才执行；修改产品或重量需覆盖确认，再进行新的报价确认。
- 显式离线组合根、鉴权与 owner 隔离、SQLite 重启恢复、消息与 JSON/SSE 重放、
  readiness、删除与退出资源关闭。主服务默认仍不装配资费，没有新增真实 transport。
- 公开报价依据 / 来源 DTO，OpenAPI、生成 TS、Web 解析、刷新恢复与独立 Eval 镜像一致。
- 业务拒绝保留稳定原因码 / 不重试；畸形响应停止展示；瞬态 HTTP 超时由 Graph 最多尝试两次。

关键决策见 [ADR 0016](adr/0016-command-bound-postage-review.md)，外部缺口见[台账](agent-kernel-phase3b-postage-gaps.md)。

## 2. 确认不是一个可复用布尔值

~~~text
Query Understanding → 产品/地区/整数克前置校验 → 冻结 Command
  → 记录待审 Command 指纹 → interrupt 展示条件与范围
  → 独立确认 → resume → 重新计算 Command 指纹
  → 相符才执行 Gateway → 校验 → 收据 → 公开报价依据
~~~

Policy 保持纯决策；decide Node 保存待确认指纹，clarify Node 处理用户恢复值；
LangGraph 负责中断 / checkpoint，不承担价格计算或业务授权判断。确认仅适用于当前
逻辑查询，不沿用到下一条新查询。请求重放仍复用已有收据，不能被解释成一次新取数。

P3 工厂设置 require_confirmation=true，此标志参与策略指纹。State v3 增加两个可空
指纹字段，旧状态缺值不是已确认；旧 P1/P2 未核验暂停查询在 P3 需要重新发起。
pricing_scope_ref 是部署赋予的非敏感计价主体 / 渠道修订号，参与 profile 指纹；
不能存密钥，也不声称已解决真实客户身份映射。API Key 的 owner 隔离与价格身份是两层边界。

## 3. 公开结果与兼容

新增 result.quote_basis，而不是直接公开领域 data.quote_basis.context：

| 字段 | 含义 / 限制 |
| --- | --- |
| schema_version | 当前 1；旧结果缺失整个 basis 时为 null |
| amount_kind / currency / product_code | 已选金额口径、显式币种与产品；必须和报价数据一致 |
| scope / is_estimate | domestic_actual_weight_no_extras / true；非最终收费 |
| fees | 白名单费用项，amount 为两位小数的十进制字符串；不推导总额 |
| fees[].included_in_amount | yes / no / unknown；当前合成燃油项为 unknown |
| source | fake_gateway / external_api、稳定名称 / profile、带时区观察时间；必须匹配 data.queried_at |

内部目录版本、计费 ID、policy 指纹、pricing_scope_ref、原始响应和凭据不公开。
provenance 继续保持轨迹专用；资费来源不携带“历史完整性”。报价依据只允许随成功资费
结果出现；未报价、失败和其他意图不能挂此对象。明确依据的总金额也规范为两位小数字符串。

旧快照仍可读取，但 UI 标明依据 / 来源未知；缺币种不默认 CNY。合成结果卡自身显示
合成警告，不依赖外层 warnings 是否保存。费用缺失不补零、费用是否包含不猜测、不自动求和。
客户端对实时 JSON/SSE 与本地快照走同一解析函数。公开合同版本为 0.3.2-phase3bp-p3，
服务包版本仍为当前开发基线 0.3.6；并不表示该包已经发布。

## 4. 离线运行与复现

组合根：[offline_postage.py](../apps/assistant-api/src/spb_assistant_api/offline_postage.py)。
测试入口：[postage_p3_fixture.py](../apps/assistant-api/tests/postage_p3_fixture.py)。
入口不读取 .env 或环境模型 / RAG / MySQL / OTel 开关；工厂只接受显式合成配置和
MockTransport，Gateway URL 固定为 .invalid。V1 工具均 unavailable。
readiness 的 ready 只证明本地依赖可用，绝不证明供应商可达。

从仓库根目录运行（使用已同步的 .venv / node_modules；每个终端单独 cd）：

~~~bash
# 终端一：使用新建的绝对临时路径，勿复用真实会话库
mkdir -p /private/tmp/postage-p3-example
cd apps/assistant-api
LANGGRAPH_STRICT_MSGPACK=true ../../.venv/bin/python -m tests.postage_p3_fixture \
  --database /private/tmp/postage-p3-example/agent.db --port 18083

# 终端二：只监听回环，不加载 .env，合成服务 Key 只在开发代理中
cd apps/chat-web
npm run dev -- --config vite.postage-offline.config.ts
~~~

打开 http://127.0.0.1:13003/。默认只有邮费试算可用，快捷示例先补产品，再确认。
合成 fixture 表只覆盖北京→上海的 SYN-A / 1250g（12.30）、SYN-A / 2000g（16.00）、
SYN-B / 1250g（18.40），不按重量计算真实价格；其他组合返回具名合成拒绝。
不在表中不表示现实不可寄递。另有 --scenario business-refusal / timeout / malformed。
本地端口被限制时需要允许回环监听；不用放开真实上游网络。

~~~bash
# 仓库根目录，全量离线验证
LANGGRAPH_STRICT_MSGPACK=true .venv/bin/pytest -o addopts='' -q

# 独立 V2 Eval 报告；ASGI + MockTransport，无网络和付费请求
cd apps/assistant-api
LANGGRAPH_STRICT_MSGPACK=true ../../.venv/bin/python -m tests.test_phase3bp_postage_eval \
  --database /private/tmp/postage-p3-example/eval.db \
  --output /private/tmp/postage-p3-example/reports

# Web；使用隔离配置构建 Agent 及既有 V1 入口，均不加载 .env
cd apps/chat-web
npm test
npm run check:agent-types
npx vue-tsc --noEmit
npx vite build --config vite.postage-offline.config.ts
POSTAGE_OFFLINE_UI_MODE=legacy npx vite build --config vite.postage-offline.config.ts
~~~

上述 cd 块分别从仓库根目录开始，不是同一终端连续粘贴。
示例路径可以自行替换为 mktemp -d 创建的目录；退出两个服务后临时库可自行归档。

## 5. 验收证据与限制

- Python：新增 35 项；全量 922 passed（12.57 秒，本机一次测试，不是 SLA）。
  覆盖产品补槽、条件修改 / 提前确认拦截、进程重建、身份漂移、鉴权 / owner / 删除、
  readiness、关闭、JSON/SSE 互相重放、拒绝 / 超时 / 畸形响应、API / Eval / OpenAPI 镜像。
- Web：新增 26 项；全量 55 passed；生成类型检查、vue-tsc 与 Agent / V1 隔离构建通过。
  覆盖选择器无默认值、独立确认、旧结果未知、费用口径、合成来源、畸形 JSON/SSE / 快照拒收。
- [development 数据集](../eval/datasets/agent-postage-workflow-development-v1.jsonl)：
  SHA256 a387582e4f83a5ef1503ceadf235f70beeeede5e8eb607cb5dd4da76466f8acc。
  独立 Runner 13/13 场景、28/28 Turn、8 次 Mock、API 错误 0；金额依据另由
  expected_quote_basis_values 断言，错误 Gold 的负向测试证明其参与通过判定。
  这是人工设计的合成工程回归；其中保守否定拒绝为已知局限，不是理解正确率。
- 浏览器：仅邮费能力可选 → 缺产品下拉 → 显示 1250g 范围确认 → 刷新仍可继续 →
  12.30 CNY / 0.80 费用包含未知 / 合成警告 → 刷新保留原观察时间 → 新查询 3000g
  返回稳定拒绝提示，没有新报价卡或“0 元”；未展示供应商私有文本。

签名、产品 / 地区字典、金额单位、费用包含关系等 P-G01～P-G12 继续 Open。
保守关键词会误拒“不需要保价”，也不能保证覆盖所有自然语言改写；新增结构化确认
不替代通用否定理解或模型 holdout。不存在真实资费网络模式，更不能以这些通过项声称生产报价正确。

## 6. 下一阶段

P3 本地范围收尾。[6A-1](agent-kernel-phase6a1-browser-identity.md) 随后已完成代理访客隔离。
[6A-2](agent-kernel-phase6a2-sqlite-recovery.md) 又完成受控 SQLite / 停服整库恢复及独立命名卷演练。
[6A-3](agent-kernel-phase6a3-controlled-deployment.md) 又完成独立 Web / Compose、HTTPS 入口、
CI 工作流和本地新卷恢复 / V1 回退；[工具链安全收口](agent-kernel-phase6a3-ci-closeout.md)
又完成 Vitest 升级和严格门禁。下一步获 Git 授权后验证远程 CI；尚未扩大真实业务调用范围。
不把合成工厂放进默认生产组合根。P4 / T4 等接口方确认、访问条件及单独调用授权；
时限文档、完整否定理解和人工审核 holdout 分别跟踪。
