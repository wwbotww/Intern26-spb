# 6A-2 独立合成存储演练

用途是验证命名卷、进程重建、整库备份与新目录恢复，不是 V2 HTTP / Web 部署。
无端口、无网络、无 `env_file`、无外部服务，模型 / 物流调用为 0。每条命令执行后退出，
所有业务输入为固定合成值；详细一致性与安全边界见 [6A-2](../../apps/assistant-api/docs/operations.md)。

## 镜像前提

当前 Dockerfile 复用本地 `intern26-spb-assistant-agent-demo:0.3.6` 的已安装依赖，覆盖仓库
中的当前 Assistant 源码。它不是独立发布镜像，也不自动获取私有配置或下载缺失依赖。
先确认本地存在此镜像；若缺失，停止本节，改用主文档的本地 Python 演练。独立镜像从
`uv.lock` 冻结构建及 digest / 平台匹配属于 6A-3，不通过改 tag 假装依赖匹配。

```bash
docker image inspect intern26-spb-assistant-agent-demo:0.3.6 --format '{{.Architecture}} {{.Id}}'
docker build --pull=false --network=none -t intern26-spb-agent-storage:6a2 -f deploy/storage/Dockerfile .
```

在仓库根目录运行。`.dockerignore` 排除 dotenv / 数据库 / 快照；构建不读取 `.env`。
本机 ARM Docker host 使用已有 amd64 依赖镜像，默认平台运行出现警告但成功；尝试显式
amd64 构建被本地缓存 manifest 拒绝。该问题已记录到 6A-3，**不用于性能或多架构结论**。

后续 [6A-3 独立部署](../agent/README.md) 已提供 digest 固定 / `uv.lock` 冻结构建的新镜像，
并完成 HTTPS / 新卷恢复 / V1 回退；不再复用上述旧 Demo。本文保留 6A-2 历史演练证据，
新的完整装配验收优先使用 6A-3；真实业务与多架构资格仍未验收。

## 全新命名项目演练

为每次完整演练选择全新 project name；不要复用生产项目名。以下包装函数只是减少重复，
不是调度任务。`--env-file /dev/null` 避免 Compose 隐式加载仓库 `.env`。

```bash
STORAGE_DEMO_PROJECT="intern26-storage-$(date +%Y%m%d%H%M%S)"
storage_compose() {
  docker compose --env-file /dev/null --project-name "$STORAGE_DEMO_PROJECT" -f deploy/storage/docker-compose.yml "$@"
}
storage_compose config --quiet
storage_compose run --rm --no-deps demo seed --directory /var/lib/spb-runtime/store
storage_compose run --rm --no-deps operator backup --source /var/lib/spb-runtime/store --destination /var/lib/spb-backups/snapshot-1
storage_compose run --rm --no-deps operator verify --source /var/lib/spb-backups/snapshot-1
storage_compose run --rm --no-deps operator restore --source /var/lib/spb-backups/snapshot-1 --destination /var/lib/spb-restored/store
storage_compose run --rm --no-deps demo resume --directory /var/lib/spb-restored/store
storage_compose run --rm --no-deps demo replay --directory /var/lib/spb-restored/store
storage_compose ps --all
```

预期：seed 的 `synthetic_tool_calls=1`，resume 为 `1`（已完成查询重放为 0、暂停查询继续为 1），
replay 为 `0`。每次 `uid=10001`、`network_calls=0`。会话 TTL 为 1 小时，过期后使用新项目。
根文件系统只读、capabilities 全部丢弃、禁止提权；三个卷的私有父目录由镜像预建归属，
空命名卷首次 copy-up 后由 UID 10001 运行，不用 root 启动 Agent 或 `chmod 777`。

| 命名卷后缀 | 容器挂载点 | 内容 |
| --- | --- | --- |
| `_runtime-data` | `/var/lib/spb-runtime` | 初始合成库 |
| `_backup-data` | `/var/lib/spb-backups` | 自包含快照与清单 |
| `_restored-data` | `/var/lib/spb-restored` | 新恢复库，后续 resume / replay 只写此处 |

`run --rm` 清理短生命周期容器，**不清理卷**。保留三个小型合成卷便于复核；需要清理时，
先按 project label 核对目标，确认仅含本次合成数据，再由操作者决定删除。不要对现有业务
Compose 使用删卷命令，不把默认项目的 `down -v` 当作通用收尾步骤。

## 本次证据

2026-09-10 使用项目 `intern26-storage-6a2-20260910`：备份 / verify / restore 均成功，
快照 200704 字节；表计数为 conversations 2、creation receipts 2、message receipts 2、
legacy tool receipts 0、v2 tool receipts 1、migration 1、checkpoints 13、writes 166。
恢复后的继续与重放实测为 1 / 0 次 Fake 调用，均成功退出；最终 `ps --all` 无容器。
三个合成卷保留供复核，不作为 RTO / RPO SLA。

所有卷只在本机；没有异地复制、加密封装或自动保留策略。原业务服务、真实凭据和现有
SQLite 数据库均不在此演练范围内。
