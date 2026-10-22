# 回归样本集

> **facts-first harness 工程的永久回归资产**
>
> 规范文档：`docs/regression_sample_spec.md`

## 目录结构

```
tests/regression/
├── investigate/           # 投标审查样本
│   ├── level1_historical/ # 历史场景的合成复刻（基础回归集）
│   ├── level2_attack/     # 幻觉攻击样本（针对性压力测试）
│   └── level3_synthetic/  # 合成对抗样本（生产 bug 沉淀，只增不删）
├── collusion/             # 串标审查样本（同 3 级结构）
├── writer/                # 标书编写样本（同 3 级结构）
├── schemas/               # ground truth pydantic schema
├── annotator/             # AI 标注 prompt + 一致性判定脚本
│   └── prompts/           # 三流程标注 prompt
├── reports/               # A/B 回归报告产出目录
└── spot_check_log/        # 人工 spot-check 日志
```

## 数据处理策略

本目录仅允许提交合成样本和无原文匿名契约样本。目录名中的 `historical` 表示复刻历史问题场景，不表示样本包含历史客户原文。

- Level 1 / Level 2 的 `source` 必须为 `synthetic_scenario_matrix`
- 原始 PDF、客户材料、人员/企业信息、私有专家标注和存储索引均不得进入 Git
- `writer/level1_historical/real_anonymized_v1/` 只允许无原文匿名契约与聚合质量快照
- 发布前运行 `python scripts/check_public_data.py`

## 样本版本号规则

`vX.Y.Z`（X=结构变更 / Y=新类别或标注 prompt 变更 / Z=单样本新增或修正）

## 每项目样本结构

```
proj_NNN/
├── inputs/            # 原始输入索引（SHA256 引用 storage/ 下 PDF）
├── expected.jsonl     # ground truth，每行一条，schema 见 tests/regression/schemas/
└── meta.yml           # 项目元信息（采样日期、标注 prompt 版本、三轮一致性、spot-check 记录）
```

## Level 3 样本沉淀规则

- 来源：已修复问题的最小合成复现，禁止复制生产原文
- 修复 PR 合入前必须完成 Level 3 样本入库
- Level 3 样本**只增不删**，即使修复完成也永久保留
