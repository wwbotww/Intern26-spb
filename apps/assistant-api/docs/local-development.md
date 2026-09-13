# Assistant 本地开发

[服务入口](../README.md) · [工作区安装与联调](../../../docs/development.md)

本页负责 Assistant 自身的合成入口、依赖隔离和后端预期；Web 的代理/启动命令归 Web README。
所有命令从仓库根目录执行。不要覆盖已有 `.env`；临时状态库和合成入口不用于真实部署。

## 五能力 Demo

本地业务 Tool 使用 fixture，价格不是实时价格。Demo 使用普通 Settings；
显式关闭模型、遥测与受控身份，避免继承已有业务开关。不能绑定外网或用于真实数据。

终端一：

~~~bash
AGENT_DEMO_DIR=$(mktemp -d)
ASSISTANT_AGENT_DEMO_DB="$AGENT_DEMO_DIR/agent.db" \
ASSISTANT_QUERY_MODEL_ENABLED=false ASSISTANT_OTEL_ENABLED=false \
ASSISTANT_AGENT_ENABLED=false ASSISTANT_TRACKING_ENABLED=false \
ASSISTANT_AGENT_MANAGED_STORAGE_ENABLED=false \
ASSISTANT_AGENT_BROWSER_SESSION_ENABLED=false \
ASSISTANT_AGENT_BROWSER_PRIVATE_HTTP_ENABLED=false \
ASSISTANT_AGENT_BROWSER_COOKIE_SECURE=true \
ASSISTANT_HOST=127.0.0.1 ASSISTANT_PORT=8081 \
.venv/bin/spb-assistant-agent-demo
~~~

另一个终端按 [Chat Web / Agent Demo](../../chat-web/README.md#agent-demo)启动匹配的 Web，
打开 http://127.0.0.1:13003/。输入“查邮件”应要求单号，补 `1234567890123` 后展示合成轨迹。
价格 fixture 为 `iPhone 16 Pro 256GB`，不是当前真实库的首页示例。
刷新等待输入页面可恢复本地快照，再由服务端继续；Ctrl-C 停止两进程，临时库保留供检查。

另一个终端可做五能力黑盒门禁：

~~~bash
.venv/bin/spb-eval agent-run --dataset eval/datasets/agent-workflow-v1.jsonl \
  --base-url http://127.0.0.1:8081 --concurrency 4 --fail-on-gate
~~~

该 Demo 的简化资费不是完整协议验收；需要产品/范围确认与 CSB Adapter 时用下一节。
模型接入另见[配置说明](integrations/query-model.md)，启用后会产生供应商用量。

## 资费协议离线演示

终端一（忽略 dotenv，只接受固定 synthetic profile + MockTransport）：

~~~bash
POSTAGE_DEMO_DIR=$(mktemp -d)
.venv/bin/python -m apps.assistant-api.tests.postage_p3_fixture \
  --database "$POSTAGE_DEMO_DIR/agent.db" --port 18083
~~~

另一个终端按 [Chat Web / 资费协议 Demo](../../chat-web/README.md#资费协议-demo)启动 Web。
同为 http://127.0.0.1:13003/，不能与上一节同时占端口。
预期只有邮费能力可用；北京→上海、1.25 kg，选择 SYN-A，确认后得到合成 12.30 CNY。
同次重放保留观察时间，新查询重新执行。可用 `--scenario business-refusal|timeout|malformed`
分别启动拒绝/超时/畸形响应夹具；不是把带竖线的整串作为参数。
fixture 之外的组合可能返回合成拒绝，不表示现实中不可寄递。

不启动浏览器/网络的 CI Eval 入口见[全局验证](../../../docs/development.md)，
评测数据/格式与门禁唯一维护于 [Eval](../../../eval/README.md)。
产品、签名、响应校验和待确认合同见[资费适配](integrations/postage.md)。

## 商品价格与候选恢复演示

独立 catalog_v2 合成入口，使用回环访客身份。该测试 helper 本身会构造普通 Settings；
下面从全新目录、清空继承环境启动，避免本地 dotenv/遥测/身份配置混入，不接模型或公司库。
API 固定 18086，Web 固定 13006；不能与其他同端口身份 QA 同时运行。

终端一：

~~~bash
CATALOG_DEMO_REPO="$PWD"
CATALOG_DEMO_DIR=$(mktemp -d)
(
  cd "$CATALOG_DEMO_DIR"
  env -i PATH="$PATH" \
    PYTHONPATH="$CATALOG_DEMO_REPO:$CATALOG_DEMO_REPO/apps/assistant-api/src" \
    "$CATALOG_DEMO_REPO/.venv/bin/python" -m apps.assistant-api.tests.product_price_browser_fixture \
    --database "$CATALOG_DEMO_DIR/agent.db"
)
~~~

另一个终端按 [Chat Web / 商品价格 Demo](../../chat-web/README.md#商品价格-demo)启动 Web。
设备可输入“查询 iPhone 16 Pro 价格”，在候选等待时刷新，再点击规格取得合成报价；
生鲜可输入“黄瓜价格”，按要求补上海和零售口径，查看来源日及原始/标准单位。
合成来源时间不保证与今天一致；旧来源日的 partial 是预期，不得改成实时价。
以上验证的是公开协议、身份和恢复，不是 V2 SQL 或真实五品牌覆盖。

## 使用真实依赖

- V1 本地启动与 dotenv 优先级见[服务入口](../README.md)，具体协议见 [V1 API](api-v1.md)。
- 受控 Agent 用 `python -m spb_assistant_api.deployed_app`，不加载 dotenv；
  服务鉴权、访客身份和 managed 存储见[服务运维](operations.md)。
- 模型是单独开关，不随 Demo 或 RAG Key 自动启用。真实联调先获授权并限制预算，
  见[Query Model](integrations/query-model.md)。
- 默认 Web 是 V1；匹配 Agent UI/身份的方式见 [Web 配置](../../chat-web/README.md)。
- 服务器端的 Web/API/代理/数据与镜像必须配套，按[跨服务运维](../../../docs/operations.md)
  和[部署手册](../../../deploy/agent/README.md)操作，不把上述 fixture 装配进去。
