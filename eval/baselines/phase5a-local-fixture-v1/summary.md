# Phase 5A Local Fixture Baseline

- 运行时间：`2026-09-04T08:39:51.462102+00:00`
- 数据集：`eval/datasets/agent-workflow-v1.jsonl`
- 数据集 SHA256：`12a42ff261085a4f00dba308525b9cc0720c377ac02f4ec3ddb8c03cf6b12298`
- 服务版本：`0.3.2`
- 场景 / Turn：`13 / 17`
- Calibration / Holdout：`7 / 6` 场景（`7 / 10` Turn）
- 质量门禁：`PASS`

| 指标 | 结果 | 分母 |
| --- | ---: | ---: |
| Intent Accuracy | 1.0000 | 17 |
| Required Input Accuracy | 1.0000 | 4 |
| Wrong Tool Rate | 0.0000 | 11 |
| Task Completion Rate | 1.0000 | 10 |
| Recovery Rate | 1.0000 | 4 |
| API Error Rate | 0.0000 | 17 |
| Turn P50 / P95 | 47.589 / 66.972 ms | 17 |

该基线使用确定性本地 fixture、Fake Shipping Gateway 和本地 SQLite，只用于验证评测
链路和回归门禁，不代表真实接口、模型或生产流量准确率。完整运行配置与机器可读指标见
同目录 `manifest.json`。
