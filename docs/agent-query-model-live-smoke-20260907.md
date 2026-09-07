# DeepSeek 意图理解真实联调记录

> 日期：2026-09-07，北京时间 11:27～11:29。
> 结论：供应商接入与本地 V2 多轮链路已验证；5 次模型请求中 4 次成功、1 次超时回退。
> 这是小样本工程烟测，不是代表性 holdout、准确率报告或生产 SLA。

## 1. 环境与方法

- 代码：`assistant-api 0.3.4`，基于 `27f735a` 的未提交工作树；Prompt 为
  `query-understanding-v2`，Hybrid Parser 为 `hybrid-understanding-v1`。
- 模型：`deepseek-v4-flash`，`https://api.deepseek.com`；JSON object、thinking 关闭、
  temperature 0、max tokens 768、总预算 8 秒、并发上限 2、响应上限 65536 字节。
- FastAPI TestClient 调用公开 `/v2/agent/messages` ASGI 路径，使用真实 HTTPX 模型
  Adapter、LangGraph 与每次独立的临时 SQLite；不是 Mock 模型，也未启动 Web／Nginx。
- 每条输入先在纯规则 Understanding 上取基线，再调用启用模型的 V2；所有输入均为
  合成文本。三个物流 Gateway 和政策／价格业务数据仍使用本地 fixture。
- 用户在本地配置并授权小额联调；Key 只从 Settings 读取。未保存凭据、请求头、完整
  Provider 响应、reasoning_content 或真实业务数据。报告仅保留人工核对的脱敏摘要。

本次实现文件 SHA256（便于在提交前区分代码快照）：

| 文件 | SHA256 |
| --- | --- |
| `adapters/deepseek_understanding.py` | `bf38e1cb148fe67e8b3af2f490fb57f35c5d4e8a0a084b1b25cc70a865d12c8d` |
| `services/query_understanding.py` | `8d49038c525f3c0fa67b8df4614a056c30daa02c89a38d473e4d6e4a92d03af3` |

## 2. 合成场景与观测

这四条首轮输入的规则基线均为 `unknown`，因此确实走到了模型 fallback，而不是用
规则命中冒充模型识别。

| 输入 | 成功模型识别与 V2 行为 | 后续验证 |
| --- | --- | --- |
| 我寄出去的那件东西，眼下在什么地方？ | `tracking` → `waiting_user`，缺 `mail_no` | 补 `1234567890123` → 合成轨迹成功 |
| 我今天交给寄递员的东西，收件人大概何时能收到？ | `delivery_time` → `waiting_user`，缺 `origin/destination` | 补“从北京市寄到上海市” → 合成时限成功 |
| 替我把这个包裹送过去，要花多少银子？ | `postage` → `waiting_user`，缺 `origin/destination/weight` | 补“从北京市寄到上海市，2公斤” → 合成资费成功 |
| 写一首关于秋天的诗 | 首次超时；人工复测成功识别 `unknown` → `handoff` | 无业务结果、无业务工具执行；不是自由写诗 Agent |

成功分类的语义 Trace 均包含 `source=model` 和 Prompt v2；三个补槽恢复的 Trace 为
`source=active_workflow`，硬字段仍来自规则。业务工具结果不由模型编造。

### 逐次模型请求

| 次序 / 首轮 | 模型调用耗时 ms | V2 首轮耗时 ms | Provider 用量（输入 / 输出 / 合计 tokens） | 结果 |
| --- | ---: | ---: | --- | --- |
| 1 / 轨迹 | 1203.428 | 1225.490 | 1939 / 57 / 1996 | 成功 |
| 2 / 时限 | 1475.503 | 1496.003 | 1943 / 73 / 2016 | 成功 |
| 3 / 资费 | 1836.820 | 1856.950 | 1940 / 79 / 2019 | 成功 |
| 4 / 无关输入 | 8008.312 | 8026.335 | 未收到 usage，未知 | `query_model_deadline_exceeded` |
| 5 / 同一无关输入，独立复测 | 1412.359 | 1431.509 | 1933 / 34 / 1967 | 成功 |

成功响应已知用量为输入 7755、输出 243、合计 7998 tokens。超时请求是否仍产生供应商
计费未知，不能当作零成本，也不能用 7998 代替最终账单总量。上述是串行、单次观测；
不据此推算 P95、线上成功率、语义准确率或并发容量。

### 规则与重放边界

共执行 18 次本地 V2 请求，只有上述 5 次尝试模型调用：

- 三个业务场景各补槽恢复一次，均不追加模型调用，并返回对应类型的 `success` 结果。
- 首轮／补槽结果共 7 次幂等重放，业务响应一致（新的 `request_id` 除外），模型新增
  调用为 0；这里只证明已保存收据后的重放，不证明模型计费 exactly-once。
- “查邮件 1234567890123”、显式 `policy` 入口和“取消”分别走 `rules`、
  `explicit_ui`、`rules`，均未调用模型。

## 3. 超时与验证中发现的问题

无关输入首次在约 8 秒触发总 deadline，日志给出 `upstream_timeout` /
`query_model_deadline_exceeded`；Hybrid 保留规则 `unknown`，WorkflowPolicy 返回
`handoff`，没有执行业务工具。V2 返回 HTTP 200 是成功投影了受控终态，不意味着模型
调用成功。此处 `handoff` 是类型化终态，尚未对接人工工单。

随后人工用新数据库／新会话发起同一输入，保持 8 秒和所有模型参数不变，在约 1.41 秒
成功返回 `unknown`。系统没有自动重试，本次复测也未从统计中删掉首次失败。当前证据
不足以判断第一次慢在网络、供应商排队还是生成过程；默认预算和 Prompt 均保持不变。

全量离线回归另发现 3 个旧 Health 测试隐式加载了本地 RAG／MySQL 配置。新增测试域的
autouse fixture，清除继承的 `ASSISTANT_*` 环境并关闭默认 dotenv 读取；测试只能显式
配置合成依赖或指定测试用 `_env_file`。用户 `.env` 未修改。三类模型补槽、首轮重放、
unknown／超时不执行工具、回退后规则仍可用均补充了 Mock 回归，普通测试不调用付费模型。

## 4. 复测与下一步

配置／启动方式见[模型接入说明](agent-query-model-integration.md)。需要手动复测时，
显式启用模型并使用新的 `ASSISTANT_AGENT_DEMO_DB`，通过本地 Agent Web 或 V2 JSON
依次发送上表首轮和补槽消息；复测会再次产生供应商用量。重复测试收据时保持相同
`Idempotency-Key` 和请求体。先关模型运行相同首轮可取得规则基线。

自动回归使用 `apps/assistant-api/tests/test_query_model_integration.py` 的合成 Mock，
不依赖 Key，也不把真实供应商调用加入默认 CI。

下一个开发切片为阶段 5 的 Understanding 质量对照：

1. 固定五意图、unknown、口语改写、歧义、多意图和跨轮更正的标注规范与分层样本；
   本次已看过的烟测输入归入 development，不能包装成未见 holdout。
2. 补 Macro-F1、硬槽位 F1、模型调用／失败回退率、延迟和已知／未知用量统计；区分
   模型正常判 unknown 与模型失败回退，固定纳入失败请求。
3. 冻结 Prompt／规则／数据版本，进行同样本 Rules 与 Hybrid 对照和错误分析；较大
   付费实验另设明确调用预算，不沿用本次“小量烟测”的授权无限运行。
4. 有质量基线后再接逐 Node OpenTelemetry span 和 Dashboard；真实物流仍等阶段 3B
   接口合同，正式 V2 发布与生产持久化验收继续保持未完成。
