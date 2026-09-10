# 6A-3 收口：Vitest 安全升级与默认离线门禁

> 状态：Remote CI verified，2026-09-10。已提交并推送 `develop`，修复基础镜像引用后，
> 提交 `d28c87c54683bdfb26f61ba89f28005c9da2e355` 的两个远程 job 均成功。
> [实际运行记录](https://github.com/wwbotww/Intern26-spb/actions/runs/34440603018)。
> 下文本地证据保留为历史基线；本次 CI 不读取业务凭据、不调用真实业务服务。

## 1. 交付与变更范围

| 项目 | 收口前 | 本次完成 |
| --- | --- | --- |
| Vitest / @vitest 系列 | 3.2.7，npm 报告 2 项 moderate | 全部锁定 4.1.11，完整 npm audit 为 0 |
| 默认 `npm test` | 依赖开发 Vite 配置；须人工指定离线配置 | 独立 `vitest.config.ts`，不加载 dotenv / 开发代理 |
| CI 安全门禁 | 仅拦截 high 及以上 | 显式包含 dev，拦截 moderate 及以上，审计失败直接失败 |
| Node / 安装合同 | CI 用 Node 22，但包未声明支持范围 | `^22.12.0 || >=24.0.0`；CI / Docker 严格检查引擎并安装 dev |
| Docker Web 构建 | 编译 / 类型检查 | 在同一固定 Node 22 构建阶段先跑 70 项单测，再编译 Agent / V1 |

本次只改工具链声明 / 锁文件、默认单测配置、部署构建与 CI；新增 3 项 Python 合同回归。
既有 70 项 Web 业务用例及应用代码没有因升级修改断言或行为。Vue、DOMPurify、marked 等
运行时依赖版本保持不变；本次版本变化全部位于开发依赖图，删除 vite-node 等不再需要的
传递依赖，不使用 `npm audit fix --force` 或跨到 Vitest 5。

## 2. 为什么这样升级

维护者公告确认 GHSA-82fw-gwwq-j7x9 涉及 Vitest / mocker 的开发服务文件读取边界，
修复版本包含 4.1.11。npm 将同一公告计入两个依赖条目，**不是两个独立漏洞**。
现有测试只使用本地 run，不表示可以保留已知漏洞；本次选择已修复的 4 系列，避免同时
引入另一个大版本的变化。依据
[维护者安全公告](https://github.com/vitest-dev/vitest/security/advisories/GHSA-82fw-gwwq-j7x9)。

逐项核对了 [4.1.11 对应的迁移指南](https://github.com/vitest-dev/vitest/blob/v4.1.11/docs/guide/migration.md)：
当前没有自定义 pool、构造器 mock、restoreAllMocks、Browser Provider 或覆盖率插件，
不需要为这些破坏性变化改写业务用例；保留现有环境 stub / fake timer 的显式清理。
V4 缩小了默认排除目录集合，因此新配置明确只收集 `src/**/*.test.ts`，避免构建产物或
临时文件成为测试来源。Vite 仍为锁定的 7.3.6，没有借安全升级顺便更换业务构建栈。

## 3. 可执行门禁

- `vitest.config.ts` 与 `vite.config.ts` 分开，`envDir: false`，不调用 loadEnv、不合并
  开发代理。测试默认 Agent UI / browser identity 关闭，身份测试自行显式开关；默认
  Node 环境，watch / API 服务关闭，无用例时失败。显式 CLI 参数仍由操作者负责。
- `npm run audit:dependencies` 统一执行 npm 官方 registry 审计，含开发依赖，门槛 moderate。
  不加 `|| true`、`continue-on-error` 或忽略列表；网络 / registry 错误同样不能视为安全通过。
- CI 的 `npm ci --include=dev --engine-strict` 与 Docker 安装一致，避免 NODE_ENV 导致
  漏装测试 / 扫描依赖，并拒绝不满足声明的 Node。CI 仍使用托管 Node 22，不用本机 Node
  版本替代其兼容性证据；Docker 固定基础镜像实测为 Node 22.23.2。
- Web 最终镜像仍只复制 dist 与 Nginx 文件，Vitest 不进入运行镜像。此次编译产物与
  上阶段相同，最终 Web 镜像 ID 也相同；升级发生在构建依赖层，不能仅凭最终镜像 ID
  判断构建工具是否更新，应结合锁文件摘要和构建内测试日志。
- `test_phase6a3_ci_contracts.py` 覆盖修复版本 / 同版本 Vitest 家族、Node / lock 一致、
  默认测试隔离、Docker 测试步骤及 CI 全依赖 moderate 门禁。静态合同不替代实际安装 /
  运行，因此另外在本机与 Docker 均执行了真实测试命令。

## 4. 验证证据

| 检查 | 2026-09-10 结果 |
| --- | --- |
| 完整 npm 安全审计 | moderate 2 → 0；升级后 info / low / moderate / high / critical 均 0 |
| 全量 Python | 1044 passed（本次新增 3） |
| 默认 Web 单测 | 70 passed，Vitest 4.1.11，本机 Node 25.8.1 |
| 环境污染回归 | 故意设置冲突 UI / identity 标志与合成代理 Key 后，默认单测仍 70 passed |
| 旧离线单测入口兼容 | 显式 `vite.postage-offline.config.ts` 仍 70 passed |
| Node 22 构建环境 | 两种 Web 镜像均执行 70 项测试、vue-tsc 与构建，全部通过 |
| 公开离线 Eval | 13 场景 / 28 Turn，8 次 Mock，真实 / 模型 0，quality gate passed |
| 类型与构建 | 生成类型一致，vue-tsc，Agent / V1 无 dotenv 本地隔离构建通过 |
| Docker HTTPS 全流程 | 四组 PASS：入口边界、双访客 / Cookie、新卷恢复 / SSE / 重放、V1 回退并返回 V2 |

本次审计对应 `apps/chat-web/package-lock.json` 的 SHA-256：

```text
d3a9904fcdc8193938f3e41caa1ae24d1e4b09be2ceee9205cdc5a664111948a
```

审计结果只表示这个锁文件在当时公告库中的已知 npm 风险；不是源码无漏洞、依赖永远安全、
Python / OS 镜像扫描通过或生产安全认证。Node 22 容器验证也不等于 GitHub 托管 runner /
完整 Linux amd64 Web 平台验收。HTTPS 新卷恢复与 V1 回退继续使用
[6A-3 演练脚本](../deploy/agent/smoke.py)，不接真实 transport，不复用原业务存储。

完整复跑项目 `spb-agent-6a3-b09e267acdff`，报告目录为本机临时目录
`spb-agent-6a3-ejhrr2u6/report.json`；`status=passed`、`containers_removed=true`。
四个合成卷（项目名前缀加 `_runtime-data` / `_backup-data` / `_restored-data` /
`_synthetic-tls`）保留供复核，不删除其他阶段的卷。运行镜像 ID 与
[初次 6A-3 证据](agent-kernel-phase6a3-controlled-deployment.md#6-本地证据与复跑)相同。

复跑默认本地门禁（依赖已安装，审计需要 npm 官方仓库网络）：

```bash
npm --prefix apps/chat-web test
npm --prefix apps/chat-web run audit:dependencies
npm --prefix apps/chat-web run check:agent-types
.venv/bin/pytest
.venv/bin/python deploy/agent/smoke.py --build
```

## 5. 后续顺序与授权边界

1. 本地测试依赖升级完成；**D02 的 npm 部分关闭**，Python / OS 扫描与补丁更新策略继续记录。
2. D01 的远程运行部分已关闭：`contracts`、`synthetic-deployment` 均成功；分支保护、
   长期报告留存与自动镜像发布仍是独立事项，不能由一次绿灯推断完成。
3. 按用户批准的范围进入 [6A-4 内网单实例更新](agent-kernel-phase6a4-intranet-release.md)：
   复用旧 RAG / SQL，三项物流查询按 unavailable 处理，不等待真实物流接口或多副本。
4. T4 / P4 需真实接口条件、合同确认及单独调用授权；holdout 需人工审核 / 冻结与新预算，
   时限仍待文档。本轮不需要 API Key，也没有消耗此前模型请求授权。

总进度见[实施方案](agent-workflow-implementation-plan.md)，部署与缺口见
[6A-3](agent-kernel-phase6a3-controlled-deployment.md)，核心取舍补入[复盘故事 U](project-retrospective.md)。

## 6. 远程执行揭示的问题与修复

首次 `d257b1d` 的 contracts 成功，部署构建失败；增加有界、转义后的合成错误注释后，
`76ba4a5` 明确定位到 Python 基础镜像的注册表引用不存在。本地缓存曾把镜像 ID 作为
可拉取摘要使用，因而本地成功不能证明干净机器可复现。

修复使用官方 `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` 实际返回的 index digest
`sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58`，保留冻结依赖。
新增 `verify_base_images.py` 在构建前从注册表核对三项固定引用，不接受本机 image cache
作为证据；引用失败时只报告白名单来源的候选摘要，**不自动换标签或跳过失败**。
Node / Nginx 原引用随后也通过远程注册表与实际构建验证，无需修改。

`d28c87c` 的 contracts 于 2026-09-10 05:20:38 UTC 完成，synthetic-deployment 于
05:22:11 UTC 完成，均为 success。实际完成冻结安装、全量测试、npm 安全门禁、离线 Eval、
双 Web 构建和 HTTPS / 身份 / 整库恢复 / V1 回退烟测；不是只看 workflow 文件或本地测试。
