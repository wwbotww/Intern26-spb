# Phase 6A-2：受控 SQLite、整库备份与独立恢复

> 状态：Local / synthetic verified，2026-09-10。承接 [6A-1](agent-kernel-phase6a1-browser-identity.md)，
> 不代表已完成 V2 生产部署、多副本协调或真实物流互通。真实 `.env`、现有数据库均未迁移。

## 1. 交付范围与一致性边界

本切片让单实例 Agent 有可操作的存储生命周期，而不只是“配置一个 SQLite 路径”：

- 专用目录、文件权限和运行 UID 校验；运行时与备份工具共享整库进程租约。
- 停服后备份完整数据库，生成版本化清单；校验后仅恢复到全新的独立目录。
- 覆盖会话 owner / TTL、创建幂等、消息幂等、Tool 收据、LangGraph checkpoints / writes。
- 恢复后验证继续补槽 / 报价确认、免调用重放、删除和绝对 TTL；独立 Docker 命名卷演练。

SQLite 事务快照完整，不等于 Agent 的业务步骤全部完成：checkpoint、工具收据和 HTTP
幂等记录并不是一个跨组件事务。因此本轮不做热备份承诺，而采用 **停止服务 → 获取租约 →
拒绝未完成 claim → SQLite Backup API → 校验 → 发布清单**。

SQLite WAL 也是数据库持久状态的一部分，不能只复制正在运行的 `agent.db`；这里调用
`sqlite3.Connection.backup`，将已提交 WAL 内容纳入自包含快照，再将输出设为 DELETE journal。
依据：[SQLite Online Backup API](https://www.sqlite.org/backup.html)、
[SQLite WAL](https://www.sqlite.org/wal.html)、
[Python sqlite3 backup](https://docs.python.org/3.11/library/sqlite3.html#sqlite3.Connection.backup)。

## 2. 模块职责

| 模块 | 职责 | 不承担 |
| --- | --- | --- |
| `storage_paths.py` | 专用路径、0700 / 0600、固定文件名、初始化和非阻塞 `flock` | 修改用户旧目录权限、分布式锁、同 UID 恶意进程防御 |
| `storage_snapshot.py` | 整库备份、摘要 / profile / 结构检查、新目录恢复 | 启动模型、解码 checkpoint 业务载荷、自动升级或修复 |
| `storage_cli.py` | 运维侧 `init / backup / verify / restore` 与安全退出码 | HTTP 管理端点、dotenv、强制覆盖、自动清理 |
| checkpointer factory / composition | 开库前获取租约，覆盖所有 Graph / 元数据连接生命周期 | 把锁、文件名或备份逻辑写入 Domain / Node |
| `storage_demo.py`、`deploy/storage/` | 有限次数、无网络的合成恢复演练 | 生产 Web / API 栈、真实 transport、常驻备份任务 |

没有变更业务 State、公开 API 或 OpenAPI；领域层不依赖新运维模块。新增配置
`ASSISTANT_AGENT_MANAGED_STORAGE_ENABLED=false` 默认关闭，与既有 V1 和未标记 SQLite 路径兼容。

## 3. 受控目录与运行时门禁

```text
store/                         # 当前运行 UID，0700，专用子目录
  agent.db                     # 0600，固定名称
  agent.db-wal / agent.db-shm   # 运行时可能存在，同样校验 0600
  agent-store.lock             # 0600，永久保留同一个锁文件
  .agent-store-reserved        # 空的初始化 / 恢复占位标记
  agent-store.json             # 最后发布的版本 / store_id 标记

backup-unique/                 # 0700，新建且不复用
  snapshot.sqlite3             # 0600，自包含快照，无 WAL / SHM
  backup.json                  # 0600，摘要、版本、结构与计数
```

只支持 POSIX 本地文件系统。拒绝相对路径、`..`、符号链接路径、硬链接数据库、宽松权限、
不同 UID 和根 / home / 当前工作目录本身；不递归创建父目录，不自动修复权限。
macOS 的 `/tmp`、`/var` 可能是链接，传入物理绝对路径，而不是自动跟随链接。

`init` 只接受不存在的新目录；`--if-absent` 只允许重新验证已受控目录，不接管旧库。
启用 managed 配置时，运行入口要求路径为受控目录中的 `agent.db`。即使开关关闭，
新版 factory 发现目录标记或占位标记也会启用门禁，不能靠关开关绕开。
**未标记旧目录仍保持旧行为，没有获得这一保护；本轮不提供旧库就地收编命令。**

租约在所有运行连接关闭后释放。第二个合作进程启动或运行中备份立即失败，不排队。
取消 / 异常 / 进程退出后内核释放租约，锁文件不删除，避免不同 inode 各自持锁。
这阻止第二个实例共享该库，不是允许多 worker 并发运行；同 UID 直接 SQLite 客户端、旧版
应用或网络文件系统不能据此获得一致性保证。备份前仍须由操作者确认实际写入方已停止。

## 4. 快照合同与拒绝策略

当前 profile：`agent-state-v3-receipt-v2-snapshot-v1`。整库包含 8 张表：

| 数据 | 表 |
| --- | --- |
| owner / 状态 / 绝对期限 | `agent_conversations` |
| 首次创建幂等 | `agent_conversation_creation_receipts` |
| 消息 claim / 完成响应 | `agent_idempotency_receipts` |
| 历史与当前 Tool 执行收据 | `agent_tool_execution_receipts`、`agent_tool_execution_receipts_v2` |
| 持久化迁移标记 | `agent_persistence_migrations` |
| LangGraph 工作状态 | `checkpoints`、`writes` |

检查 SQLite integrity / foreign keys、精确表 / 字段 / 主键 / 类型、迁移标记、State 1/2/3
元数据版本、未完成消息 claim、缺失 owner 元数据或已删除会话的残留工作状态。
清单记录 SHA-256、字节数、schema 摘要、各表行数、备份时间、源 store_id 和三个 LangGraph
依赖版本。恢复要求 profile 和依赖版本匹配，不借恢复自动升级 Checkpointer。

恢复首先验证源备份，再创建新目标；复制后重新校验字节摘要、结构及计数，最后发布新的
store_id。不会修改源会话 ID、owner、TTL、请求 hash、checkpoint、收据或报价时间。
运行时可以按原有规则迁移受支持的旧 State；备份工具本身不反序列化或迁移业务对象。

默认 30 秒 / 256 MiB，上限 120 秒 / 1 GiB；设置错误、超时、大小超限、版本不符、
完整性失败、额外 sidecar 或摘要不符均不发布成功输出。已有目标永远不覆盖。
失败的新目录保留 0700 / 0600，可能有部分文件但没有有效发布标记；不能直接启用，也不
自动递归删除。调查后由操作者决定保留或清理，重试使用另一个全新目录。

CLI 成功退出 `0`，存储操作失败退出 `2`，输出固定错误码或表计数，不打印原始 SQL / 状态 /
路径 / 上游异常。`store_busy` 表示先停服；`unfinished_requests` 表示核对中断请求和收据，
**不能删除 claim 后自动重试**。`target_exists` 应更换新目标，不 chmod / 覆盖旧库。

## 5. 本地可复跑的纯合成验收

在仓库根目录、依赖已安装后运行。以下只创建新的临时数据，不读取 `.env`，不启动 HTTP
服务，不使用模型或真实 Gateway；所有样本为合成输入，保留目录便于检查。

```bash
STORAGE_DRILL_ROOT="$(mktemp -d)"
STORAGE_DRILL_ROOT="$(cd "$STORAGE_DRILL_ROOT" && pwd -P)"
.venv/bin/python -m spb_assistant_api.storage_demo seed --directory "$STORAGE_DRILL_ROOT/runtime"
.venv/bin/python -m spb_assistant_api.storage_cli backup --source "$STORAGE_DRILL_ROOT/runtime" --destination "$STORAGE_DRILL_ROOT/backup"
.venv/bin/python -m spb_assistant_api.storage_cli verify --source "$STORAGE_DRILL_ROOT/backup"
.venv/bin/python -m spb_assistant_api.storage_cli restore --source "$STORAGE_DRILL_ROOT/backup" --destination "$STORAGE_DRILL_ROOT/restored"
.venv/bin/python -m spb_assistant_api.storage_demo resume --directory "$STORAGE_DRILL_ROOT/restored"
.venv/bin/python -m spb_assistant_api.storage_demo replay --directory "$STORAGE_DRILL_ROOT/restored"
```

`seed` 建立一个已完成和一个待补槽会话，Fake 调用为 1；`resume` 先重放已完成请求
（0 新调用），再继续待补槽会话（1 新调用）；重新启动进程执行 `replay` 为 0 新调用。
演练会话固定 1 小时 TTL，恢复和重跑不续期，过期后使用全新演练目录。

如果是新建受控服务目录，而非演练，用同运行 UID 调用：

```bash
.venv/bin/python -m spb_assistant_api.storage_cli init --directory /absolute/private-parent/new-agent-store
```

然后由部署配置显式映射 managed 开关与 `.../new-agent-store/agent.db` 路径。此命令只
初始化受控文件，必须由正式运行时完成建表，尚未建表的空库不能备份；不要通过复制旧 DB
覆盖空文件来绕开迁移验收。正式环境切换、同源 Cookie 配置及 V1 回退属于 6A-3。

## 6. 验证证据（2026-09-10）

新增 44 项 Python 回归，覆盖：

- 同 / 跨进程租约互斥、异常与取消释放、进程被终止后释放、不删除锁文件。
- 路径 / UID 权限合同、链接拒绝、原库不覆盖、超时 / 超限 / 半成品不发布。
- 8 表备份、WAL 已提交内容、篡改 / 版本 / schema / orphan / 未完成 claim 拒绝。
- P3 公开 API + 6A-1 Cookie + SQLite：恢复后原 owner 的创建 / 消息重放不调用供应商，
  待确认报价继续后仅 1 次 Mock；删除后不能重放，新访客不能接管旧会话。
- 原始绝对 TTL 不变、过期拒绝与 janitor 清理；环境中误设模型开关不影响离线 CLI。

Docker 演练详见 [独立存储栈](../deploy/storage/README.md)：使用三个全新 named volumes，
UID 10001、根文件系统只读、`network_mode: none`、无端口 / dotenv。每条命令使用新容器，
验证容器销毁后卷内会话仍可恢复。快照 200704 字节、2 个会话、13 个 checkpoints、166 个
writes、1 个 v2 Tool 收据；这些只描述该合成夹具，不是吞吐、RTO 或业务规模。

测试文件：`test_phase6a2_storage_paths.py`、`test_phase6a2_storage_snapshot.py`、
`test_phase6a2_storage_demo.py`。全量 Python **1020 passed**、Web **70 passed**，生成类型
一致性检查、`vue-tsc --noEmit`、Agent / V1 无 dotenv 隔离构建通过。Docker seed / resume /
replay 实测为 1 / 1 / 0 次 Fake 调用，恢复 / 校验均退出 0；最终无运行容器，保留三个合成卷。

```bash
.venv/bin/pytest
npm --prefix apps/chat-web test -- --config vite.postage-offline.config.ts
npm --prefix apps/chat-web run check:agent-types
```

Web 单测使用无 dotenv、身份默认关闭的 offline 配置，身份测试自己显式开关；浏览器身份
QA 配置用于 Agent 构建 / 人工浏览器演练，不用于强制所有旧客户端测试打开身份门禁。
本轮曾误用后者导致 4 项旧客户端测试因缺少核验而失败，修正验收命令后 70 项通过，未改
业务代码绕过身份检查。下一步状态见[实施方案](agent-workflow-implementation-plan.md)。

## 7. 未完成项及恢复操作注意事项

| 缺口 | 当前限制 / 后续动作 |
| --- | --- |
| 并发 / crash 业务原子性 | 租约不是业务事务；拒绝未完成 claim，不修复任意 crash 窗口，不保证上游 exactly-once。后续独立故障恢复方案再引入修复工具 |
| 数据隐私与备份信任 | DB / 快照可能含原始输入和结果；0600 与 Git / 镜像忽略不是加密。SHA-256 不是签名，不能接收不可信备份；磁盘加密、签名 / 密钥托管、异地与保留策略未实现 |
| 恢复的业务合法性 | 结构一致不验证所有 checkpoint 业务语义；同配置 / 同依赖恢复，先隔离核验，再决定切换。不得自动调用真实工具来“确认备份有效” |
| 身份与删除历史 | Cookie 签名密钥 / Origin / 代理 Key 不在备份中，需独立保管与匹配；恢复不延长 Cookie 或 TTL。恢复到删除前的快照可能带回后来删除的数据，尚无备份外删除账本 |
| 老库迁移与版本 | 不自动接管未标记旧库、不跨版本恢复；生产升级 / 迁移 / 回退需先记录版本与恢复点，再单独演练 |
| 持久卷部署 | 本轮仅独立合成存储栈；随后 6A-3 已验证独立 V2 Compose / TLS / Host / 公共路由、新卷恢复 / V1 回退。CI 文件已实现，远程与目标业务部署待验收 |
| 镜像复现 | 本轮复用旧镜像，ARM host / amd64 image 有平台及缓存 manifest 限制；随后 6A-3 已新增 digest 固定、uv.lock 冻结构建的独立镜像，不再复用该 Demo。两轮均不作为多架构或性能验收 |

后续 [6A-3：受控 V2 部署与实际 CI](agent-kernel-phase6a3-controlled-deployment.md) 已完成
独立 Compose / 环境映射、HTTPS 入口、CI 工作流和新卷恢复 / V1 回退的本地合成验收，
保持真实物流和模型关闭。随后[工具链安全收口](agent-kernel-phase6a3-ci-closeout.md)也已完成，
下一步需获 Git 授权后验证远程 CI；T4 / P4、代表性 holdout、
生产多副本仍分开验收。关键设计决策见 [ADR 0018](adr/0018-quiescent-agent-storage-snapshots.md)。
