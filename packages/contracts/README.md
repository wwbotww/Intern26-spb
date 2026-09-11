# 共享数据契约

[项目全貌](../../README.md) · [全局模块边界](../../docs/workspace-architecture.md)

本包只维护 [Offline Pipeline](../../apps/offline-pipeline/README.md)写入和
[RAG API](../../apps/rag-api/README.md)读取之间的数据约定，不包含 I/O 或业务路由，
也不是全项目的通用 DTO 仓库。Assistant 的公开 HTTP 契约不放进本包。

## 当前契约与来源

| 内容 | 代码事实 |
| --- | --- |
| collection | [collection.py](src/spb_contracts/collection.py)：schema version 1、database `aisv`、collection `spb_policy_chunks`；主键、文本、Dense/Sparse 和必需字段集合 |
| embedding | [embedding.py](src/spb_contracts/embedding.py)：`moka-ai/m3e-base`、768 维、L2 归一化、`COSINE`、最大序列 512 tokens |
| chunk metadata | [metadata.py](src/spb_contracts/metadata.py)：来源、文档/父文档、正文/embedding 输入、章节、顺序、内容摘要和抓取状态 |

文件产物使用的 `ChunkMetadata` 与 Milvus 字段名不完全相同，写入边界负责转换；
不能把 TypedDict 当作未经转换的数据库实体。实际完整字段始终以代码为准。

## 变更约束

1. 先判断是否改变向量空间、主键稳定性或旧数据可读性；不兼容时使用新版本/collection。
2. 同步生产端写入/验证与读取端启动/检索检查；不要仅在一个应用覆盖默认值。
3. 更新相关 fixture 和测试，再检查数据源交接与迁移目标；不能在测试里自动重建业务 collection。

从仓库根目录验证，无需连接实际 Milvus：

~~~bash
.venv/bin/pytest packages/contracts/tests apps/offline-pipeline/tests apps/rag-api/tests
~~~

各模块如何运行见各自 README；数据所有权与真实更新流程见
[数据源边界](../../docs/data-sources.md)。
