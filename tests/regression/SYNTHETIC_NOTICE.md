# SYNTHETIC NOTICE - 合成样本声明

> **偏离 #003**（B1, 2026-04-17）

本目录下的 Level 1 / Level 2 样本均为**场景矩阵合成数据**，由以下工具链产出：

- 场景矩阵由生成器和各流程 `README.md` 共同约束；公开仓库不依赖内部规划文档
- Level 1 生成器：`scripts/baseline/scenario_generator.py`
- Level 2 生成器：`scripts/baseline/attack_generator.py`
- 标注 runner：`tests/regression/annotator/annotator_runner.py`

## source 标签

所有样本 `meta.yml` 的 `source` 字段统一为 `synthetic_scenario_matrix`，
与 B0 的 `synthetic_framework_validation` 严格区分。

## 局限与校准

合成样本的分布可能偏离真实投标市场。I-009 要求在真实生产数据可得时必须校准：

- 在私有环境用真实 ground truth 校准，但公开数据始终保持合成或匿名聚合
- 用真实流量重采基线指标时只发布不可逆的聚合结果
- 最晚 B5 结束前或 Lite 转正式版时完成

## Level 3 保持空置

Level 3 对抗样本仅从真实 bug 沉淀，不用合成数据伪造。

## regression_run.py 兼容

`regression_run.py` 的 `--allow-synthetic` 开关同时覆盖
`synthetic_framework_validation` 与 `synthetic_scenario_matrix` 两种 source。

## 当前样本规模

| 流程 | Level 1 | Level 2 |
|---|---|---|
| 投标审查 | 50 项目 | 8 类 x 3 条 = 24 条 |
| 串标审查 | 25 对 | 8 类 x 3 条 = 24 条 |
| 写标 | 20 节 | 8 类 x 3 条 = 24 条 |
| **合计** | **95** | **72** |
