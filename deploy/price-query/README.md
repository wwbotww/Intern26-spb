# V2 价格只读 SQL 隔离验证

本入口只验证消费者固定 SQL、金额/单位和关系约束，不采集网页、不接业务库、不读取 `.env`，
不代表生产迁移、真实五品牌覆盖或数据新鲜度已验收。

从仓库根目录使用已安装的 Python 依赖运行：

```bash
.venv/bin/python deploy/price-query/mysql_smoke.py --mysql 5.7.36
.venv/bin/python deploy/price-query/mysql_smoke.py --mysql 8.4
```

需要 Docker。脚本仅接受源码内固定的两份官方 MySQL 镜像摘要；默认只用本地缓存，
显式 `--pull` 才允许下载所选摘要。CI 使用相同脚本及固定摘要。
可用 `--python /path/to/installed/python` 指定测试解释器。

每次运行独立创建随机名称的容器、数据库和合成凭据，只发布随机回环端口，数据放在 tmpfs，
不复用现有容器、宿主数据目录、开发数据库或业务配置。MySQL 8.4 测试显式启用 native
password，并通过 `authentication-policy=mysql_native_password` 固定包括 bootstrap root
在内的测试账户策略，避免默认 caching_sha2 认证隐含依赖 API 锁文件未选择的 `cryptography`。
这不是生产版本/认证配置建议或生产认证兼容性验收。
退出时核对完整容器 ID 和本次标签，再删除且只删除本次容器及可重建测试数据。
脚本拒绝 TCP/SSH Docker endpoint，解析并固定本地 Unix socket，避免环境变量将容器建到远程。

测试由独立 writer 装载、变异合成行；reader 只有指定 V2 表的 SELECT 权限。
先验证没有 V1 表，再创建无 reader 权限的 V1 空壳表验证相同查询。测试还拒绝 reader 写入。
DDL 是消费投影的最小测试结构，不是生产者完整 schema、数据库约束测试或迁移脚本。
不需要生产者仓库，夹具不会访问记录中的官方 URL。

普通 `pytest` 默认跳过真实数据库测试。专用 runner 才设置 `RUN_PRODUCT_PRICE_MYSQL_TESTS=1`
及 `PRICE_QUERY_TEST_WRITER_DSN` / `PRICE_QUERY_TEST_READER_DSN`；fixture 强制检查两 DSN
同一回环地址、随机测试库前缀、显式测试用户及不同凭据。不要手工改为公司库 DSN。
失败输出不打印 DSN，运行产物仅放私有临时目录，不上传 CI artifact。

详见[实施计划](../../docs/product-price-implementation-plan.md)与
[消费合同](../../apps/assistant-api/docs/integrations/product-price.md)。
