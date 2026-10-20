# 投标审查回归样本

公开数据规范见仓库根目录 `docs/test-data-policy.md`。

## Level 1（历史场景合成复刻）

- 目标规模：50 项目 x 每项目 ~200 条审查结果
- 初版（B0.7）：10 项目
- 覆盖维度：QUAL/TECH/COMM/PERF/STAFF/FORMAT 六类；否决项占比 >= 20%；>= 10% 含扫描件/图片

## Level 2（幻觉攻击）

- 每流程 >= 45 个，覆盖 8 类攻击场景（空返回/截断/编造/领域错配/矛盾/边界/模板混淆/编号日期歧义），每类 >= 2

## Level 3（合成对抗）

- 来自生产 bug，只增不删

## Ground Truth 字段

见 `tests/regression/schemas/investigate_expected.py`

| 字段 | 类型 | 说明 |
|------|------|------|
| requirement_id | int | 关联招标要求 |
| expected_compliance_level | fully/partially/not/missing | 人工标注正确结论 |
| expected_is_compliant | bool | 人工标注 |
| expected_risk_level | high/medium/low/none | 人工标注 |
| expected_relevance_score | 0-100 | 人工标注 |
| expected_evidence_pages | list[int] | 人工核验的真实页码 |
| expected_failure_state | 五状态枚举 | 理想失败合同状态 |
| is_critical | bool | 否决项/高金额/高风险 |
| expert_note | str | 标注理由 |
| bug_trace | str/null | Level 3 来源 BUG 编号 |
