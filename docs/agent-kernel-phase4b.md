# Phase 4B：Versioned SSE 与 Stateful Agent Web

> 状态：2026-09-04 已完成不依赖真实物流接口的交互切片。
>
> 发布边界：V2 仍只在显式注入 Agent 依赖时挂载，默认 `main.app` 与现有 V1 Web
> 保持不变；`agent_demo` 是仅用于本地验收的 Fake Gateway composition root。
>
> 后续状态：本文保留 Phase 4B 里程碑事实；readiness、低基数指标、脱敏 Run Trace 和
> janitor 调度已在 [Phase 4C](agent-kernel-phase4c.md) 完成，V1 政策/价格能力复用已在
> [Phase 4D](agent-kernel-phase4d.md) 完成。

## 1. 本阶段完成内容

Phase 4B 把 Phase 4A 的 JSON 用户链路扩展为可恢复的浏览器交互闭环：

- `POST /v2/agent/messages` 支持版本化 SSE，JSON 与 SSE 共用同一会话、幂等、容量和
  timeout 执行路径；
- SSE 投影只包含公开 DTO，不暴露 LangGraph node 名、内部 Graph State、消息历史或
  checkpoint 内容；
- Web 类型由版本化 OpenAPI artifact 确定性生成，收到 SSE 后仍执行运行时 schema
  校验；未知事件安全忽略，已知事件结构或版本非法时显式失败；
- Agent Web 提供五类能力入口、自由输入、显式意图选择、多意图澄清、结构化补槽、
  取消、幂等重试、刷新恢复和服务端会话清理；
- 轨迹、时限、资费以及政策/价格依据使用独立 Result Renderer，不把不同领域数据压成
  一个通用文本卡片；
- `AgentApiDependencyFactory` 把 Agent SQLite/checkpointer 生命周期纳入 FastAPI
  lifespan；本地 `agent_demo` 显式装配三个 Fake Gateway，用于无网络 E2E。

该切片没有假装提供 token 级模型流。服务先发 `status`，工作流到达公开停止态后再投影
`state`、类型化结果和 `delta`。以后接入模型 token 时可以扩展 `delta` 的产生时机，但
不能直接透传 `astream_events`。

## 2. SSE 契约

所有已知事件都带 `schema_version: "1"`。正常序列为：

```text
status -> state -> (input_required | result) -> [delta] -> done
```

流建立后的执行异常使用 `error` 终止，不再尝试改变已经发送的 HTTP 200；请求体、必填
头等可在建流前确定的错误仍返回普通非 2xx JSON。

| 事件 | 公开内容 | 终止 |
| --- | --- | --- |
| `status` | Request ID、接收阶段和用户可读状态 | 否 |
| `state` | conversation/turn ID、公开 phase、intent、next action | 否 |
| `input_required` | 服务端 interrupt 投影出的输入描述 | 否 |
| `result` | 判别式结果、公开 failure 摘要和 warnings | 否 |
| `delta` | 用户可见回复片段 | 否 |
| `done` | 经过 `AgentResponse` 再校验的最终快照 | 是 |
| `error` | 脱敏 code/message、原始 HTTP 语义和 retryable | 是 |

`stream` 不参与创建或消息业务指纹，因此同一个业务请求可以先走 JSON、再用相同
`Idempotency-Key` 走 SSE 重放；两次返回相同 conversation/turn，不会重复推进图或调用
工具。这让传输恢复与业务幂等保持正交。

## 3. Web 模块边界

```text
OpenAPI artifact
  -> generate-agent-api-types.mjs
  -> generated/agent-api.ts
  -> agent-api.ts (HTTP/SSE + runtime validation)
  -> AgentApp.vue (conversation orchestration)
       -> AgentSlotForm.vue
       -> AgentMessage.vue
            -> results/* typed renderer
  -> agent-session.ts (local resumability only)
```

- `agent-api.ts` 是唯一理解 wire event 的前端模块；Vue 组件不解析原始 JSON。
- `agent-session.ts` 最多 30 分钟保存公开消息、`conversation_id` 和未完成请求的幂等键，
  超时后自动删除。它不保存 Assistant Key、上游 Key、Graph State 或隐藏推理；公开消息
  仍可能包含用户输入的邮件号，因此生产环境还需完成告知、脱敏与保留策略评审。
- 刷新后的等待输入由本地公开快照重绘，真正的 workflow 继续仍以服务端 SQLite
  checkpoint 为准。
- 流在 `done/error` 前断开时保留原请求，用户点击“安全重试”会复用相同幂等键并清空
  已显示的部分内容，避免重复拼接。
- 页面通过 `VITE_ASSISTANT_UI_MODE=agent` 显式切换到 V2；未设置时继续加载原 V1 页面。

生成与校验命令：

```bash
cd apps/chat-web
npm run generate:agent-types
npm run check:agent-types
```

## 4. 本地无密钥演示

本地演示只提供轨迹、时限和资费三个 Fake 能力；政策与设备价格会在能力目录中显示为
“当前未装配”。它不调用外网，也不需要 API Key：

```bash
uv run --package spb-assistant-api spb-assistant-agent-demo

cd apps/chat-web
VITE_ASSISTANT_UI_MODE=agent \
CHAT_WEB_ASSISTANT_API_URL=http://127.0.0.1:8081 \
npm run dev
```

默认 SQLite 文件位于系统临时目录，可用 `ASSISTANT_AGENT_DEMO_DB` 指定其他本地路径。
Demo 入口显式关闭鉴权，只能用于本机开发；正式部署仍必须由反向代理持有服务 Key，
浏览器不得接触凭据。

## 5. 自动化与浏览器验收证据

本阶段新增后端 SSE/生命周期测试，覆盖：

- 完成、等待输入、建流后脱敏失败和建流前 schema 失败；
- SSE 不包含 Graph 内部字段；
- JSON/SSE 跨传输重放保持同一 conversation/turn；
- Demo lifespan 启动/关闭 SQLite 组件，三个 Fake 能力目录可见。

前端新增 8 个测试，覆盖生成契约的运行时消费、跨 UTF-8 chunk 解析、未知事件、非法
已知事件、断流可重试、终止 error、能力发现、本地恢复状态和 30 分钟清理。当前 Web 共
15 个测试，
类型检查与 production build 通过。

浏览器以真实 Vite proxy + FastAPI Demo 验收了：

1. 完整邮件号直接路由并展示轨迹时间线；
2. 缺少邮件号时触发 interrupt，刷新页面后补槽表单仍存在；
3. 提交结构化邮件号后从同一 checkpoint 完成；
4. 时限与资费分别展示路线、时长和 Decimal 金额 Renderer；
5. 同时要求轨迹和资费时展示意图候选，选择后继续正确工具；
6. 390 × 844 视口无页面横向溢出，浏览器控制台无 error/warning。

## 6. 尚未完成

- 轨迹、时限、资费仍是 Fake Gateway；真实 URL、认证、wire schema 和错误码必须等接口
  材料后在 Phase 3B 实现，届时才需要对应 API Key；
- 政策与设备价格尚未通过 V2 compatibility adapter 注册到 Agent Registry；
- 默认生产 `main.app` 尚未启用 V2，Compose 也仍构建 V1 Web；
- 多副本共享持久化仍未完成；Phase 4C 已补 V2 readiness、低基数指标、脱敏 Run Trace
  和 janitor 调度，但尚不是完整 node/edge 分布式 Trace；
- 当前 SSE 是稳定生命周期流，不是模型 token 流，也未提供基于 `Last-Event-ID` 的偏移续传；
- 浏览器恢复依赖本机 `localStorage` 保存公开 UI 快照，不等同于跨设备历史查询。

V1 能力复用 Adapter 随后已由 Phase 4D 完成；收到真实接口合同与密钥后再进入
Phase 3B。生产化进展见 [Phase 4C](agent-kernel-phase4c.md)和
[Phase 4D](agent-kernel-phase4d.md)。
