# Agent 交付摘要与历史证据

归档日期：2026-09-10。本文压缩开发过程，保留可追溯里程碑、独立实验和发布证据；
不是当前运行手册或待办。当前状态见[状态页](../current-status.md)，后续见[路线图](../roadmap.md)。
2026-09-11 的全品类限定发布增补于本文末节；前文日期对应各自历史时点。

## 1. 已收口的工程阶段

| 历史标识 | 完成交付 | 当前查阅位置 |
| --- | --- | --- |
| Phase 0～2 | Spike、Kernel、Hybrid、SQLite、补槽恢复 | [运行设计](../../apps/assistant-api/docs/runtime.md)、ADR 0001～0005 |
| Phase 3A | Gateway Port、HTTP 单次尝试、Failure/重试/熔断基础 | 运行设计、ADR 0006 |
| Phase 4A～4D | V2 JSON/SSE、Web、readiness、V1 Tool 复用 | [API](../../apps/assistant-api/docs/api-v2.md)、[架构](../workspace-architecture.md) |
| Phase 5A/5B | 多轮 HTTP Eval、门禁/对比、语义 Trace、故障矩阵 | [Eval](../../eval/README.md)、ADR 0008/0009 |
| 模型接入、5C/5D | 真实模型 Adapter、组件同样本对照、人工审核冻结工具、V2 Mock 回归 | [模型接入](../../apps/assistant-api/docs/integrations/query-model.md)、下述实验 |
| Phase 5E | 逐 Node span、OTLP、Prometheus/Tempo/Grafana 本地闭环 | [监控栈](../operations.md#本地合成监控栈)、ADR 0011 |
| Tracking T0～T3 | 临时协议、查询作用域、公开来源、受控装配/浏览器 | [轨迹合同](../../apps/assistant-api/docs/integrations/tracking.md)、ADR 0012/0013 |
| Postage P0～P3 | 领域前置、双层签名、确认、公开报价依据及离线 Web/Eval | [资费合同/缺口](../../apps/assistant-api/docs/integrations/postage.md)、ADR 0014～0016 |
| 6A-1～6A-4 | 匿名身份、整库恢复、CI、旧主机兼容与内网发布 | [跨服务运维](../operations.md)、[状态库](../../apps/assistant-api/docs/operations.md)、ADR 0017～0019 |

真实物流 T4/P4、时限供应商接入、独立真实 holdout 和多副本没有被这些阶段验收覆盖。
旧 Phase 文件中的“下一步”已被当前状态/路线图替代，不再保留大量逐篇跳转。

## 2. 历史数据与 RAG 基线

早期数据流水线记录：292/292 正文、144 附件中归档 139、38 个扫描附件/500 页 OCR；
生成 12,163 chunks，768 维，最长 419 tokens；增量复用 11,034、新计算 1,129。
5 个不可恢复历史附件保留失败/父文档 lineage。数字是该批次结果，不是实时索引保证。

早期 rag-api 0.5.0 私有 80 样本（48 可答/32 拒答）记录：
Recall@5=1.0000、MRR@5=0.8469、错误拒答 0/48、错误回答 0/32、
引用 Gold 命中 1.0000、必要事实覆盖 0.9948；串行 Chat P50/P95=6.47/8.97 秒。
另一次 24 条并发样本 P95=40.67 秒，说明排队是明确限制。
这些沿用原复盘历史记录，原始私有报告不随 clone 分发；不得作为当前生产准确率/SLA。

## 3. 2026-09-07：真实模型接入烟测

DeepSeek v4-flash、Prompt query-understanding-v2、8 秒总预算、无自动重试。
四条合成首轮规则均为 unknown；三个物流意图识别并补槽到 Fake 结果，无关输入首次超时，
人工独立复测返回 unknown。累计 18 次本地 V2 请求，模型 **5 次：4 成功、1 超时**；
7 次已完成消息重放不追加模型调用。成功已知 7,998 tokens，超时用量未知。

调用的是本地 ASGI V2 与真实模型 HTTP Adapter，不是浏览器/代理/真实物流。
保留首次失败；人工复测不是系统自动重试。小样本不推算准确率、并发容量或 SLA。
当时基于 27f735a 后的工作树，模型 Adapter/Understanding 文件摘要分别为：
`bf38e1cb148fe67e8b3af2f490fb57f35c5d4e8a0a084b1b25cc70a865d12c8d`、
`8d49038c525f3c0fa67b8df4614a056c30daa02c89a38d473e4d6e4a92d03af3`。

## 4. 2026-09-07：Rules / Hybrid 组件对照

48 条 synthetic development/draft，含 4 条 seen_smoke；实现、规则、字典和 Prompt 固定，
先导出无 Gold 输入，两组观测独立重算。同样本 28 条免模型、20 次授权请求，串行、8 秒、无重试。
只测 Understanding，不运行 Graph/物流/RAG/价格 Tool，不是 holdout。

| 指标 | Rules | Hybrid |
| --- | ---: | ---: |
| 完整样本通过 | 34/48 | 48/48 |
| Intent Macro-F1，固定六类 | 0.7068 | 1.0000 |
| Intent Accuracy | 30/44 | 44/44 |
| 联合硬槽位 micro-F1 | 0.9600 | 1.0000 |
| 硬槽位 TP / FP / FN | 24 / 0 / 2 | 26 / 0 / 0 |
| 缺槽集合正确 | 39/46 | 46/46 |
| 实际模型调用 / 全部样本 | 0/48 | 20/48（41.67%） |
| 模型失败 / 实际尝试 | N/A | 0/20 |
| 预算跳过 / 未观测行 | 0 / 0 | 0 / 0 |
| 已知 total tokens / 用量未知调用 | 0 / 0 | 40,097 / 0 |
| 模型调用 P50 / P95 | N/A | 725.34 / 999.20 ms |
| 组件 P95，48 条混合路径 | 0.08 ms | 933.03 ms |

Intent 分母 44（排除 2 控制/2 多意图）；硬槽位 46 行、26 个 Gold 原子值。
14 条改善、0 条退化；20 次中 14 次改善业务意图、6 次正常 unknown，失败回退为 0。
收益来自语义选择后规则重提，不是允许模型生成可信硬实体。
延迟仅为本次串行组件观察，40,097 是已知 token 用量而非货币账单。

| 对象 | SHA256 |
| --- | --- |
| 数据集原始文件 | `598868598a5f9d3ab872e0e36ba02c9daa682ee3fb3de9d4a8103030308d47eb` |
| 无 Gold 请求规范化指纹 | `a95fe6e8c79bab3cf71195812d8faf9366d2961b5f1c5bb58b13e75f5c4a9b88` |
| 两组共同的组件实现 | `76ad8c56419f6467fc3360e3b52cd9a4badc6956b1008cdc41ed666fc8a759bf` |
| 两组共同的 Prompt／schema | `fd9b96fe4a9e0551227077ff8278e0aeae6e12d34e3bedfae0d42cf4bc3f5eb1` |
| Rules 非敏感配置 | `f35461029098f47a9bc5b9b261519668ad8250ff94c7dcc85d806d76be43e172` |
| Hybrid 非敏感配置 | `19f0049f330c3cd3666ef73ee03a3a97953e0bfae1adba73b7f55f8454029b82` |
| Rules 原始 observation | `355c78342a2578ea89cef158743be3e67e0a25ee817dce0e786ab858f7f08656` |
| Hybrid 原始 observation | `3b741d255d6670e40f146495aa8d55bfc308cb7d83d3e9527507f15be230dac7` |
| 对照 report.json | `fa69d9bd94a3d88c7dd0dfe3ddabec4bf90449a14b380f1fd1a82871b6e4ec5c` |

原始私有产物位于 Git 忽略的 `eval/reports/understanding-phase5c/`：
`requests.json`、`rules-final.jsonl`、`hybrid-live.jsonl`；
正式对照子目录 `comparison/20260907-041516-03d68783/`。
持有这些文件才能无付费重算；文件不随 clone 分发，摘要不替代原始观测。
本批 20 次与上一批 5 次严格分开，旧授权均不代表后续可继续调用。

## 5. 离线回归与观测证据

- 五能力 HTTP fixture：13 场景/17 Turn，通过七项门禁；
  [精简 baseline](../../eval/baselines/phase5a-local-fixture-v1/summary.md)保留分母/数据指纹。
- Understanding → V2：13 场景/28 Turn，28 次额外幂等消息重放不追加 Mock 模型，
  三类任务覆盖应用重建后恢复；[manifest](../../eval/baselines/phase5d-scripted-provider-v1/manifest.json)。
  这是 scripted provider 工程回归，不是模型准确率。
- 严格资费：13 场景/28 Turn、8 次 Mock、真实物流/模型 0；数据指纹
  `a387582e4f83a5ef1503ceadf235f70beeeede5e8eb607cb5dd4da76466f8acc`。
  本地浏览器验证产品选择、范围确认、刷新、报价来源和拒绝无假结果。
- OTel 合成栈：5 次 invocation、2 次 API 重放、5 条 Workflow Trace、24 Node spans；
  PromQL 与匿名只读 Trace 下钻验证。不是线上分布式追踪或生产 SLO。
- 存储：停服 8 表快照、新卷恢复，seed/resume/replay 为 1/1/0 次 Fake；
  保留 owner/绝对 TTL/收据，拒绝未完成 claim。演练不证明任意 crash exactly-once。

测试文件仍保留历史 phase 命名，便于 Git 和回归定位；文档整理不删除测试、fixture、
baseline、OpenAPI、生成类型或部署脚本。复跑统一见[开发指南](../development.md)。

## 6. 2026-09-10：CI 与内网发布

- 首次本地缓存掩盖了镜像引用错误；增加 registry digest 校验后，
  `d28c87c` [远程 CI 成功](https://github.com/wwbotww/Intern26-spb/actions/runs/34440603018)。
- 旧主机 clone3/seccomp 兼容入口经原生 Linux CI 验证；
  `6a7a79a` [CI 成功](https://github.com/wwbotww/Intern26-spb/actions/runs/34444339412)。
- 最终 Web 改为同版官方 Debian Nginx；`580908c`
  [CI 成功](https://github.com/wwbotww/Intern26-spb/actions/runs/34451105696)，
  对应 Python 1089 / Web 70、离线 Eval、两模式构建与 HTTPS/private-http 恢复/回退。
- `277cfba` 收尾文档的 [CI 成功](https://github.com/wwbotww/Intern26-spb/actions/runs/34453179576)。
  以上是指定提交的历史结果，不表示任意后续 HEAD 自动通过。

获准沿用原 HTTP 入口、RAG/价格源；API 无公开 host port，新建 managed 状态库，
保留旧 Web/API/RAG。旁路、服务器停服 backup/verify、正式 HTTP 与双访客/JSON/SSE 已验。
三项物流 unavailable，不补槽、不 Fake、不在 UI 解释开发阶段。

发布镜像 **config digest（不是 registry manifest digest）**：

- API 基于 6a7a79a：`sha256:d4f86766deb8ee33745862ffa2a63fc25c31222d11bd243d849ef485fcd73be2`。
- Web 基于 580908c：`sha256:3e1234c2299d52a49732b43554a889411266bf11a91f999fd05ed3ec8c269281`。

RAG 当时集合 12,163，一次政策 SSE success 约 11.86 秒；价格库当时为空，正确 no_match。
这是时点烟测，不是持久业务状态或 SLA。浏览器自动操作两次超时，未声称目标浏览器验收。
真实正式入口未执行回退，完整恢复/回退证据来自合成演练。
私有 active-release.json、acceptance-580908c.json、镜像/配置/备份和回退脚本不复制进仓库。

## 7. 同日后续：价格与首页修正

只读复查：product=103、sku=1114、price_current=1114，5 个品牌；不是继续空库。
“iPhone 16 Pro 256GB”缺覆盖，应 no_match；“理赔材料”虽召回高相关条文，
但缺材料清单，llm_rejected 有依据，没有放松型号/引用约束来强行回答。

新示例“投诉渠道”返回 4 条政策证据，约 9.39 秒；
“iPhone 17 256GB”返回 5 条价格候选，约 0.30 秒。均为一次真实 SSE 观察，不是 SLA。
本地浏览器另验证通知 fixed 层不改变聊天区尺寸、6 秒消失；两种证据不混算。

`a2fb398` 将临时通知、阻断身份提示、经数据验证的示例目录分开；
本地 Python 1089 / Web 89、类型/双构建通过。此记录不确认该 Web 已部署，
实际版本以私有发布 manifest 为准。

## 8. 文档处置与恢复

本次删除 25 篇 `agent-kernel-phase*.md`、旧大实施方案、独立模型烟测/对照和首页过程报告。
不是判定其中内容全无价值：重复进展/逐条测试计数删除，有效合同/操作转入专题，
唯一实验/发布证据压缩到本文；复盘改为设计故事，19 篇 ADR 保留原决策理由。

删除前的完整已提交文档在 `a2fb398` 可找回，例：

~~~bash
git ls-tree -r --name-only a2fb398 docs
git show a2fb398:docs/agent-workflow-implementation-plan.md
git show a2fb398:docs/agent-kernel-phase3b-tracking-t2.md
~~~

上述只读查看不会覆盖当前工作区。临时日志路径、重复命令和中间构建 ID 不再常驻主导航；
需要完整逐次调查过程时从 Git 查阅，不新建一批归档副本。

首次文档压缩验收（层级调整前）：Python 1089 / Web 89 通过，生成类型一致；离线 Eval 13 场景 / 28 Turn、
8 次 Mock，真实/模型请求均为 0。检查 238 处本地文件链接无缺失，变更文档中的
43 个 Shell、8 个 JSON 和 1 个 JavaScript 代码块语法通过；未重跑实际服务器或 Docker 发布。

## 9. 2026-09-11：全品类价格限定发布

`5b2e637` 交付 V2 事实/只读核心；`67ab3ed` 交付 C～D7、公开候选/价格、Web/Eval 和 State 4。
首次远程 MySQL job 的 `--package` 参数误将根 dev 组解释为 API 子项目依赖组；
本地 dry-run 复现后由 `f27993b` 修正，未跳过数据库门禁。
旁路另发现官方容量数字脚注与结构化过滤不兼容，`10da6b9` 添加严格格式兼容及负向回归。
最终代码 [CI 34588550884](https://github.com/wwbotww/Intern26-spb/actions/runs/34588550884)
已 completed/success，包含全部四个 job。

本地最终 Python `1900 passed, 52 skipped`，52 项专用 SQL 场景由 MySQL 5.7.36/8.4.11
各 `57 passed` 单独覆盖；Web `114 passed`、生成类型、vue-tsc、双模式构建及两套离线 Eval 通过。
最终 API 的 private-http 合成恢复/回退再次通过；原生 legacy/seccomp 分支以远程 Linux CI 为证。

产物来源与目标主机 **config digest**：

- API：标准 `deploy/agent/Dockerfile.api`、冻结依赖、源码 `10da6b9`，
  `sha256:6e08e261aeebf091ca96cef6f8fd285408138a112dfb681efb17c245736d9c0a`。
- Web：源码 `67ab3ed` 的隔离构建与当前 Nginx 配置；本机 Docker Hub 认证端点超时，
  使用先前已发布的同版官方 Debian Nginx 1.31.3 缓存运行层，离线 COPY 当前产物组装。
  不使用旧静态页面、不改变服务器安全配置；标准 Web Dockerfile 的新构建另由远程 CI 验证。
  `sha256:c29b11f37625fbed484bdf216b464325c14b5f66143be548f412e91157583ac1`。
- 最新 API 传输归档 SHA256：`7dff6f99d46d0b6a7261e68852b851beefc8b01ebd7de8ce8ecd1f0ea9fd694e`；
  Web 初始双镜像归档 SHA256：`5c4d52c7c86646de1f469ae455bd834573df22d57248255e1142eef7d92ee579`。
  传输两端比对一致；归档/配置/回滚脚本仅保留服务器私有发布目录。

18:26（中国标准时间）原入口 `http://10.3.7.164:3000/` 切换成功。
先独立新状态旁路验证，再停止旧 Agent Web/API，完成 backup/verify/restore；
3,080,192 字节整库快照、8 张表恢复到全新 runtime，未让旧镜像打开 State 4。
保留原 RAG/数据库/签名/Origin/模型配置，显式启用 catalog_v2；未变更业务数据、账号权限或共享 daemon。
新容器非 root、只读根文件系统、drop ALL、NNP 和原 seccomp；只有 Web 绑定原私有 IP 的 3000。

实际正式入口验证：iPhone 17 256GB 候选→精确重读→SSE 重放同事实；
上海黄瓜补地区/零售→原始元/500g 与元/kg；来源日 8 月 25 日，当日请求 partial；
快递投诉政策一次真实 SSE success；物流三项正常 unavailable，无 Fake。
双访客读取/删除/选择拒绝、owned snapshot、静态与私有路由边界均通过。
从当前设备追加验证正式 JS/CSS 与测试构建哈希一致，真实候选和两类价格通过 Web 运行时校验。
只清理本次成功验收产生的会话，不删除业务资料或用户历史；失败旁路状态隔离保留。

目标内网浏览器自动化两次超时，未完成目标页面点击/刷新验收；本地合成浏览器交互证据独立记录。
旧容器和原 runtime 保留，回滚命令写入私有 active-release.json；未为演示再执行一次正式回滚。
五品牌完整等价、生产 SELECT-only 账号、来源更新策略、可选历史及旧实现清理继续按 PRICE-G 推进。
这次发布不是“全品类/全部型号覆盖”、模型 holdout 或生产 SLA 声明。

## 10. 2026-09-13：阶段收口与文档审查

以 `823a212` 的应用代码为基线复核，当前项目转入维护；轨迹/资费/时限真实接口作为遗留保留，
不为收口补造配置或删除已有 Adapter/Mock。已实现的 catalog 共享核心、公开契约、候选循环和 State 4
同步到根/服务/API/Web/Eval 文档；路线图不再把已完成的公开价格或旧首页发布列为在途任务。
压缩当前状态页的中间阶段日志，保留有日期的数据与发布边界；原详细过程可由
`git show 823a212:docs/current-status.md` 恢复。ADR 保留原决策理由，复盘只写已交付能力。

本次复跑：Python `1900 passed, 52 skipped`（专用隔离 SQL）；Web `114 passed`，
生成类型检查、vue-tsc、Agent/V1 两种隔离构建通过；资费 13 场景/28 Turn、商品 7 场景/9 Turn 的公开门禁均通过。
检查全仓 Markdown 本地链接/锚点及修改文档的 Shell/JSON/JavaScript 语法，
最小 API 示例以合成 fetch 验证两种价格 profile；文档中的隔离 catalog API 启动及 live 健康检查通过。
未调用真实模型、供应商或业务数据库，未修改 `.env`、业务代码、服务器或数据。
本地本次未复跑 Docker/MySQL/浏览器发布演练；推送提交对应的远程 CI 应单独核验，不冒用此前发布结果。
