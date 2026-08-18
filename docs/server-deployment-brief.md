# Demo 服务器部署简要说明

本文记录中国邮政理赔智能咨询 Demo 的当前服务器部署方式，供开发、测试和后续
维护使用。该方案以方向验证为目标，不代表正式生产环境基线。

> 状态说明：阶段 5 已于 2026-08-17 完成灰度部署，并于 2026-08-18 完成
> 设备价格匹配规则补丁升级。当前 Web 已切换到统一
> `assistant-api`，政策工具调用 `rag-api`，设备价格工具只读访问测试 MySQL。
> 2026-08-11 的政策问答容器以停止状态保留为回滚点。

## 部署结果

| 项目 | 当前配置 |
|---|---|
| 最近部署日期 | 2026-08-18 |
| 服务器 | `10.3.7.164` |
| 操作系统 | CentOS 7，x86_64 |
| 容器运行时 | Docker 1.13.1，无 Docker Compose |
| 访问地址 | `http://10.3.7.164:3000` |
| 对外发布 | 仅 `chat-web` 的 3000 端口 |
| Assistant API | 仅在项目 Docker 网络内监听 8081 |
| RAG API | 仅在项目 Docker 网络内监听 8080 |
| Milvus | 使用公司内网现有实例，在线服务只读访问 |
| 设备价格 | 使用公司测试 MySQL，当前服务只读访问 |

页面请求通过同源 `/api/` 路径由 Nginx 转发到 Assistant API。浏览器不直接访问
Assistant/RAG API、Milvus、MySQL 或 DeepSeek，也不会接触服务 API Key。

```text
浏览器
  -> 10.3.7.164:3000
  -> intern26-spb-chat-web (Nginx)
  -> intern26-spb-assistant-api:8081
     -> intern26-spb-rag-api:8080 -> Milvus + DeepSeek
     -> 设备价格 MySQL（只读）
```

## 服务器目录

```text
/opt/intern26-spb/
  config/
    rag-api.env       # RAG、Milvus、DeepSeek 配置
    assistant-api.env # Assistant、RAG 内部调用和只读 MySQL 配置
    chat-web.env      # Web 代理使用的 Assistant 服务 API Key
    chat-web.env.pre-assistant-20260817 # 上一版 Web 配置备份
  images/
    intern26-spb-assistant-api-0.3.2-amd64.tar
    intern26-assistant-web-amd64.tar
    spb_rag_api-0.5.1-py3-none-any.whl
    spb_assistant_api-0.3.1-py3-none-any.whl # 上一版服务器补丁构建材料
```

`config/` 及其中配置文件仅允许 root 访问，配置文件权限为 `0600`。文档、日志
和命令输出中不得记录密码、DeepSeek Key、Milvus Token 或服务 API Key。

## 容器与网络

| 名称 | 镜像 | 网络 | 端口 |
|---|---|---|---|
| `intern26-spb-chat-web` | `intern26-spb-chat-web:0.2.0-amd64` | `intern26-spb` | `10.3.7.164:3000 -> 80` |
| `intern26-spb-assistant-api` | `intern26-spb-assistant-api:0.3.2-amd64` | `intern26-spb` | 不映射宿主机端口 |
| `intern26-spb-rag-api` | `intern26-spb-rag-api:0.5.1-amd64` | `intern26-spb` | 不映射宿主机端口 |

三个容器使用 `unless-stopped` 重启策略。服务器 Docker 服务目前没有启用开机
启动，因此服务器重启后需要先由管理员执行：

```bash
systemctl start docker
```

Docker 启动后，未被手动停止的项目容器会根据重启策略自动恢复。

## 常用运维命令

查看运行状态：

```bash
docker ps
docker inspect intern26-spb-rag-api
docker inspect intern26-spb-assistant-api
docker inspect intern26-spb-chat-web
```

查看日志：

```bash
docker logs -f --tail=100 intern26-spb-rag-api
docker logs -f --tail=100 intern26-spb-assistant-api
docker logs -f --tail=100 intern26-spb-chat-web
```

停止与启动：

```bash
docker stop intern26-spb-chat-web intern26-spb-assistant-api intern26-spb-rag-api
docker start intern26-spb-rag-api intern26-spb-assistant-api intern26-spb-chat-web
```

重新启动：

```bash
docker restart intern26-spb-rag-api intern26-spb-assistant-api intern26-spb-chat-web
```

## 健康检查

从能够访问公司内网的终端执行：

```bash
curl http://10.3.7.164:3000/
curl http://10.3.7.164:3000/api/health/live
curl http://10.3.7.164:3000/api/health/ready
```

期望结果：

- 首页返回 HTTP 200；
- `live` 返回 HTTP 200，表示进程存活；
- `ready` 返回 HTTP 200，并显示 `auth`、`policy_knowledge`、`device_price`、
  `metrics` 均为 `ready`，`routing` 为 `explicit`；
- `docker ps` 显示 RAG API 与 Assistant API 均为 `healthy`；
- 宿主机没有监听 8080/8081，仅监听 `10.3.7.164:3000`。

本次部署已验证政策真实检索和引用、价格只读查询、缺参/无匹配/跨类别拒答以及
SSE。10 条私有烟测在 5 并发下全部通过，另有 5 个并发 SSE 请求完整收到
`status -> evidence -> delta -> done`（政策请求含 `usage`），未出现流错误。

## 服务 Key 边界

Web 代理使用 `CHAT_WEB_ASSISTANT_API_KEY`，该值必须是
`ASSISTANT_API_KEYS` 中的一项；`ASSISTANT_RAG_API_KEY` 则单独用于
`assistant-api -> rag-api`，两类 Key 不能混用。MySQL DSN 只存在于权限为
`0600` 的 `assistant-api.env`，不能进入 Web 环境、镜像或仓库。

## CentOS 7 / Docker 1.13 兼容处理

该服务器 Docker 版本较旧，其默认 seccomp 规则与 Python 3.12/现代 glibc 的
线程创建机制不兼容。默认配置下会出现：

```text
RuntimeError: can't start new thread
```

因此当前对 RAG 和 Assistant 两个 Python 容器使用容器级
`--security-opt seccomp=unconfined`；RAG 配置同时限制模型线程数：

```text
--security-opt seccomp=unconfined
OPENBLAS_NUM_THREADS=1
OMP_NUM_THREADS=1
MKL_NUM_THREADS=1
NUMEXPR_NUM_THREADS=1
TOKENIZERS_PARALLELISM=false
```

该设置不修改宿主机或其他容器，但单线程推理会增加检索和重排序延迟。阶段 5
10 条混合样本的端到端 P95 为约 26.7 秒，主要来自政策检索、Judge 和生成；5 个
并发 SSE 请求墙钟约 14.2 秒。后续升级到受支持的 Docker/CentOS 版本后，应
重新验证默认 seccomp，并移除该兼容参数和不必要的线程限制。

## 更新部署

服务器为 x86_64，开发机生成镜像时必须明确构建 `linux/amd64`，不能直接上传
ARM64 镜像。当前服务器没有 Compose，更新流程如下：

1. 在构建机生成并检查三个 `linux/amd64` 镜像；
2. 使用 `docker save` 导出镜像并计算 SHA-256；
3. 将镜像文件传入 `/opt/intern26-spb/images/`；
4. 在服务器校验 SHA-256 后执行 `docker load`；
5. 先更新并验证 RAG API，再启动 `assistant-api`，最后更新 chat-web；
6. 完成首页、统一健康接口、政策问答、价格查询和拒答测试；
7. 确认 8080、8081 均未发布到宿主机，且历史业务容器状态未发生变化。

更新 `.env` 时应先在本地核对变量，并保持服务器文件权限为 `0600`。不要通过
命令行参数直接传入密钥，以免出现在 shell 历史或进程列表中。

## 回滚原则

更新前保留上一个可用镜像标签。新版本异常时：

1. 停止并删除本项目对应的新容器；
2. 使用原来的容器参数和上一版本镜像重新创建；
3. 验证 `live`、`ready` 和真实问答；
4. 不停止、删除或重建服务器上的历史业务容器。

当前保留的回滚容器为：

```text
intern26-spb-rag-api-rollback-20260817
intern26-spb-chat-web-rollback-20260817
```

两者均为停止状态。Assistant 是本次首次部署，没有旧版规范容器；需要回滚时先
停止新 Web/Assistant/RAG，再恢复上述两个容器名称和旧 Web 配置备份。删除镜像、
容器、网络或归档前，应先确认目标名称严格属于 `intern26-spb` 项目。
