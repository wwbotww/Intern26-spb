# Phase 3B-T / T2：查询新鲜度、执行收据与轨迹来源

> 日期：2026-09-09。状态：T2 离线实现与回归完成，T3 / T4 待完成。
> 新增 58 个测试；完整 Python 工作区 `585 passed`，进入本轮前为 `527 passed`。
> 迁移验收使用临时 SQLite 库；没有真实物流请求、付费模型调用或 `.env` 改动。
> 生产组合根仍不启用轨迹 / V2。本轮没有提交或推送 Git。
> 后续进展：[T3 受控装配、来源展示与语义熔断](agent-kernel-phase3b-tracking-t3.md) 已完成
> 本地验收。本文保留 T2 当时的边界；下一阶段状态以 T3 / 总实施方案为准。

本切片承接 [T0/T1 契约与适配器](agent-kernel-phase3b-tracking.md)，实现其 T2 检查点。
它解决“新查询”和“同一次执行重放”的混淆，不承诺上游数据实时性或跨系统 exactly-once。
核心决策见 [ADR-0012](adr/0012-query-scoped-tool-receipts.md)。

## 1. 修复了什么

旧实现用 `(conversation_id, argument_fingerprint)` 查找 Tool 收据，并按相同参数推导
`tool_call_id`。这能减少 checkpoint 重放请求，却也让同一会话的新查询命中旧结果。
空结果同样会被缓存；工具调用预算和 reused 遥测也会把新查询误记为已执行的逻辑调用。

现在：

| 场景 | 查询 / 执行身份 | 结果与调用行为 |
| --- | --- | --- |
| 同 HTTP 幂等键、相同请求，已有完成收据 | 不进入 Graph | 返回原响应，不刷新查询时间 |
| 同查询从已保存的执行前 checkpoint 重放 | 保持 query_id / tool_call_id | 复用执行收据，返回该次查询原结果 |
| 补槽、澄清、有限重试 | query_id 保持，消息 turn_id 可变化 | 同一组参数对应同一 tool_call_id |
| 同会话新消息开始新的查询，即使单号相同 | 新 query_id / tool_call_id | 重新请求 Gateway，记录本次观察时间 |
| 先查无结果，再主动查询 | 新执行身份 | 可取得后来出现的轨迹，不复用旧 no_match |
| 取消 / 重置后重新开始 | 新查询清空旧执行兼容标记 | 不继承之前的执行收据作用域 |

这里的“新鲜”仅指本次重新向已配置 Gateway 取数，不代表供应商没有延迟或返回了完整历史。
主动重新查询必须使用新的 HTTP 幂等键；沿用旧键表示重放，不是刷新。

## 2. 四个身份各司其职

| 身份 | 用途 | 生命周期 |
| --- | --- | --- |
| `conversation_id` | owner 隔离、会话与 LangGraph thread 关联 | 整个会话 |
| `turn_id` | 一次 API 消息 / invocation 关联 | 每条消息；同幂等消息重试稳定 |
| `query_id` | 独立业务查询的执行作用域 | 开始查询时创建，跨补槽和重试保持 |
| `tool_call_id` | 一组已校验参数在该查询内的逻辑执行 | 跨实际重试和 checkpoint 重放保持 |

`argument_fingerprint` 仍是规范化 Command JSON 的 SHA-256，但只承担参数一致性校验，
不再作为会话级结果缓存键。生成方式仍是排序字段、紧凑 JSON 和 UTF-8，兼容旧指纹。

当前实现：

- `ingest` 以会话和**初始消息**的 turn_id 确定性生成 query_id，并写入 State v3。
  后续 resume 只更新消息身份，不重新生成 query_id；从 ingest checkpoint 重放也保持确定性。
- Policy 在 query_id 命名空间内按会话、工具名、参数指纹生成 tool_call_id。
- 内存和 SQLite Repository 都改按 `(conversation_id, tool_call_id)` 查找；参数指纹不唯一。
- Executor 同时检查 Command 指纹、收据会话、工具、调用 ID 和结果工具身份，拒绝不一致。
- Policy 和执行计数改按 tool_call_id 判定是否为同一次逻辑调用，不能因历史参数相同绕过预算。

query_id 不作为公开 API 字段，也不加入指标标签或 Trace。程序内直接调用 Runtime 时，
不同查询应使用不同初始 turn_id；公开 API 由服务端负责产生这些身份。

## 3. 两种迁移分开处理

### 3.1 State v1/v2 → v3

`AgentStateMigrator` 保持纯函数，不修改传入字典。新增 query_id 和可选
`legacy_tool_call`，既有槽位和审计记录保留；未知版本、无效新查询身份或伪造的旧执行
身份会失败关闭。新完成执行同时更新会话元数据中的 State 版本，保留 owner / 创建时间。

旧 checkpoint 只有在保存了可校验的 `pending_action=invoke_tool`，且旧 UUID 和 Command
指纹一致时，才获得该旧执行 ID 的兼容引用。引用只存 ID、工具名和指纹，不复制原始参数。
新查询的 ingest 必须清除该引用；没有明确 pending invocation 时，不从历史参数或
`tool_calls` 猜测旧收据属于当前查询。

迁移不再只是调用后丢弃结果：每个实际 Node 执行前应用迁移，并把增量与 Node update
一起交给 LangGraph 保存。保留 sync/async 形态，不新增图节点，不用 `update_state`
强行重写运行中的 interrupt，也不复制整段 reducer 历史。跨 SQLite 连接重建后的旧
补槽 checkpoint 和已知旧 pending Tool checkpoint 均有测试。

边界：旧恢复点若已经消耗预算、又丢失足以识别当前执行的 pending action，可能安全终止
并要求开始新查询；不能为了声称兼容而猜测缓存命中。旧完成 HTTP 收据作为历史响应保留。

### 3.2 SQLite 收据索引迁移

- 新建 `agent_tool_execution_receipts_v2`，主键为会话和 tool_call_id；旧表保留。
- 在单个 `BEGIN IMMEDIATE` 事务中按每批 128 行读取旧表，校验列 / payload 一致性，
  保留原始 receipt JSON 和执行 ID，复制到新表；全部成功才写一次性迁移 marker。
- 非法 JSON、身份碰撞或列不一致时回滚全部复制，原行保持不变，启动报稳定的
  `tool_receipt_migration_failed`，不暴露 payload。重复启动不重复复制。
- 运行时只查新表，不按参数回退到旧表。旧无 profile 的来源读取为未知版本空值，
  不自动补成当前供应商版本。
- TTL 和主动删除在事务中同时删除两张收据表；删除计数以新表的逻辑收据为准。
  创建绑定和会话 tombstone 沿用原策略，避免旧请求复活已删除会话。
- readiness 校验迁移 marker、目标列和复合主键，不能只凭同名表存在就判 ready。

这是代码层的单实例升级路径，不是生产升级演练。实际升级前应停止旧实例并备份完整
SQLite 快照（包含 WAL 的一致性处理）；不得让新旧二进制同时写同一个库。保留旧收据表
不等于可以只回滚代码：旧程序不理解新作用域和新 State，回退需要配套一致快照。

## 4. 补上收据写入和公开结果边界

API 消息 turn_id 由已认领的规范化幂等键、会话和请求 hash 确定性生成，原始键不进入
Graph。若图已保存**该消息的最新停止态**，但之后完成 API 收据的写入失败，重试可以
从该停止态补写收据，不再次执行图。测试覆盖直接查询、补槽完成和等待输入，以及关闭
SQLite 连接后的恢复；等价的空白规范化幂等键也保持同一身份。

仍有明确边界：

- 该修复不搜索任意历史消息，也不解决硬崩溃遗留的 IN_PROGRESS 租约回收；
- 上游已经执行、但 Tool 收据尚未提交的崩溃窗口仍可能造成重复只读请求；
- 单进程会话锁不等于跨进程锁，SQLite 提交不等于供应商账单事务；
- 不宣称模型或物流计费 exactly-once。

Executor 现在先校验结果，再保存成功 / no_match 收据；命中收据时也重新校验。Graph
保留独立 `validate_result` 节点，在 `compose_response` 前再校验恢复的结果，覆盖旧
checkpoint 已越过旧版本校验节点的情况。非法事实不进入新收据，也不能靠跳过节点显示。

新增轨迹不变量：成功 / partial 必须包含节点、非空状态和节点描述；来源观察时间必须与
数据查询时间一致。原单号一致、时区和时间正序约束保留，不因这次变更推断业务终态。

## 5. Gateway 返回事实，也返回观察上下文

内部 Port 改为：

```text
TrackingCommand → TrackingQueryResult
  data: TrackingData | None
  source: {source_type, source_name, profile}
  queried_at: aware datetime
  history_completeness: complete | partial | unknown
```

`TrackingSource` 来自可信 Adapter 代码，不读取供应商 payload 中自称的 source 信息。
Postal Gateway 标记 external_api / postal-tracking / 当前 provisional profile；Fake 明确
标记 synthetic，并按注入时钟记录观察时间。裸 `TrackingData` / `None` 不会被 Tool 默认为 Fake。

Tool 映射到领域 `SourceReference` 的 `source_type`、`source_name`、`source_profile` 和
`queried_at`。空结果也保留这些信息。来源是系统配置声明，不是对供应商真实性、授权范围或
签名互通的第三方认证；Mock 下的 external_api 表示测试真实 Adapter 路径，不表示调用过公网。

结果文案不再声称“最新轨迹”；no_match 明确“不代表邮件不存在”。未知完整性附带历史 /
最终状态限制；明确 partial 对应 partial 状态。现有邮政 profile 始终保持 unknown，
不会因为未满 30 条就宣称历史完整。

**V2 的投影边界未在本轮扩展。** 当前公开 `result` 只有 type / status / data / reason_code；
领域 provenance 保存在 Tool 结果、收据和 checkpoint，HTTP 仅沿用现有 warnings 投影。
T3 必须同步设计公开来源结构、更新 OpenAPI / 生成类型和 Renderer，不能仅靠替换 Gateway
就宣称来源展示已完成。本轮没有修改 Web 或进行浏览器验收。

## 6. 熔断评审结论

保留单次 HTTP 与 Graph 唯一重试所有者。T2 不另套一层业务熔断，避免 HTTP 与业务层
双重记账；现有熔断仍只覆盖 HTTP / transport / JSON。供应商 schema、业务拒绝和领域
不变量失败在 Graph / Trace 中归类，但不累计到共享 HTTP 熔断器。

T3 装配时应评审统一的“合同校验通过才记成功”边界；真实业务码 / SLA 的阈值校准待 T4。
当前不能宣称连续业务契约失败会打开熔断器，也不能用 errorDesc 文本猜可重试原因。

## 7. 验收证据

新增测试：

- `test_phase3b_execution_scope.py`：21 项，双后端新鲜度 / 历史重放、补槽 / retry、
  cancel/reset、预算、身份 / 指纹冲突和非法事实不缓存；
- `test_phase3b_scope_migration.py`：18 项，旧 State / 收据、事务回滚、不重建已删数据、
  双表 TTL / 删除、readiness、旧 SQLite checkpoint 恢复和元数据版本；
- `test_phase3b_tracking_observation.py`：19 项，来源 / 空结果 / 完整性、邮政 Mock 经
  Graph / SQLite / V2 的新查询与重放，以及 API 收据写失败后的停止态修复。

同时修正旧测试中“新查询应复用旧结果”的断言；旧 checkpoint 重放和 API 幂等断言继续保留。
部分旧测试的“有状态但无节点”成功 fixture 补为明确的合成节点，另加无节点失败用例。

```bash
# 本轮新增：58 passed
LANGGRAPH_STRICT_MSGPACK=true .venv/bin/pytest -o addopts='' -q \
  apps/assistant-api/tests/test_phase3b_execution_scope.py \
  apps/assistant-api/tests/test_phase3b_scope_migration.py \
  apps/assistant-api/tests/test_phase3b_tracking_observation.py

# 完整 Python 工作区：585 passed
LANGGRAPH_STRICT_MSGPACK=true .venv/bin/pytest -o addopts='' -q
```

全量回归包含既有五意图、SQLite、JSON/SSE、OpenAPI、理解模型 Mock 和逐 Node OTel 测试。
没有新增第三方依赖；T0/T1 的 84 项协议测试仍保留，且改为检查观察封装内的 data。

## 8. 下一阶段：T3

1. 默认关闭的显式 tracking 配置与受控 V2 组合根；保留鉴权、owner 隔离、lifespan 和 readiness。
2. 将已确认的领域来源 / profile / 查询时间投影到公开契约，生成前端类型并更新 Renderer。
3. 没有合同的时限 / 资费保持 unavailable，政策 / 设备价格继续复用既有 Tool。
4. 统一合同成功判定与熔断记账，完成 Mock 故障矩阵、JSON/SSE、Web 新查询 / 重放展示验收。
5. T4 再确认 endpoint、独立签名向量、时区 / 空结果 / 权限等并另获真实调用授权。

当前不需要用户提供物流凭据，不能将 T2 的 Mock 集成证据写成真实接口已上线。
