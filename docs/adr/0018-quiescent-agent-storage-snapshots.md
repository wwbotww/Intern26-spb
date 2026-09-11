# ADR 0018：用停服整库快照恢复 Agent，而不只保存 checkpoint

> 决策记录：下文验收范围保留为记录当时的事实；现状见[当前状态](../current-status.md)。


- 状态：Accepted / 6A-2 local verified
- 日期：2026-09-10
- 前序：[0004](0004-memory-and-checkpoint-boundaries.md)、[0012](0012-query-scoped-tool-receipts.md)、[0017](0017-browser-visitor-identity.md)

## 背景

LangGraph checkpoint 只描述工作状态。恢复时如果丢失 owner 元数据、请求幂等或 Tool
收据，图可能存在但无法安全授权、无法重放既有响应，甚至重新执行工具。即使这些记录
位于同一 SQLite 文件，它们的提交也不是一个业务级事务；文件可打开不是充分验收条件。

## 决策

1. 使用当前 UID 的专用 0700 目录、0600 文件、固定 `agent.db` 与版本标记。默认不开启
   严格配置以保留 V1 / 旧 Demo；新版 factory 自动识别受控标记，未标记旧库不自动接管。
2. 运行时与运维 CLI 共用持久锁文件上的非阻塞 `flock`，覆盖所有运行连接生命周期。
   服务运行时拒绝备份，第二个合作实例拒绝开库；不删除锁文件以避免 inode 分裂。
3. 只承诺停服 / quiescent 备份。备份前拒绝未完成消息 claim、未知 schema / State 元数据
   版本与孤儿工作状态。用 SQLite Backup API 包含已提交 WAL，而不是直接复制活动主库。
   依据：[SQLite Backup API](https://www.sqlite.org/backup.html) 与 [WAL](https://www.sqlite.org/wal.html)。
4. 整库覆盖 owner / TTL、创建与消息幂等、旧 / 新 Tool 收据、迁移、checkpoints / writes。
   清单记录 profile、依赖版本、结构摘要、行数、大小、时间与 SHA-256；不记录原始业务值。
5. 只向新目录恢复，不提供 overwrite / force。先验证备份，再创建目标，复制后复验，
   最后发布新 store_id；会话身份、原始绝对期限、请求和收据不改写。
6. 设置时间 / 大小上限，失败输出没有有效发布标记，保留私有半成品供调查，不自动删除。
   备份 CLI 不加载 dotenv、不启动应用、不反序列化 checkpoint 业务对象或调用上游。
7. 用公开 API 的 owner / 确认恢复 / 删除 / TTL 测试，加独立断网 Docker 命名卷的有限
   合成演练验收。重放不得额外调用 Fake / Mock 工具，暂停流程继续只执行预期的那一次。

## 取舍

- 热快照虽然可保证 SQLite 事务一致，但不能直接证明跨组件业务步骤已落稳；本轮选择
  短暂停服的单实例操作模型，换取简单可验证的恢复点。未完成 claim 只拒绝，不自动修复。
- 数据库快照保留 checkpoint 之外的授权与幂等约束，比仅导出聊天记录或 Graph State 更完整。
  没有把存储运维放入 Domain / Node；Agent 图和供应商适配层继续复用原有职责。
- SHA-256 只能发现意外损坏，不能防止能改写快照与清单的攻击者；权限不是加密。
  不接受不可信备份；同 UID 非合作写入、分布式锁、任意 crash repair、跨系统 exactly-once、
  备份外删除账本、跨版本迁移、异地加密与保留策略均未实现。
- 恢复数据库不会恢复浏览器本地历史或签名密钥，不延长 TTL；备份时间之后的更新 / 删除
  不在该恢复点中。独立核验通过不等于允许切换生产服务或重新访问真实供应商。
- 本轮 Docker 仅为合成存储测试，不代替 6A-3 的镜像构建、真实代理入口、CI 与 V1 回退。

实现、44 项新增回归和可复跑操作见 [6A-2](../../apps/assistant-api/docs/operations.md)。
