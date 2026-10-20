# 串标审查回归样本

公开数据规范见仓库根目录 `docs/test-data-policy.md`。

## Level 1（历史场景合成复刻）

- 目标规模：20 项目 x 每项目 ~50 对
- 初版（B0.7）：5 项目
- 必选场景：HIGH >= 5 / MEDIUM >= 6 / LOW >= 4 / NONE >= 3 / INCONCLUSIVE >= 2

## Level 2（幻觉攻击）

- 每流程 >= 45 个，覆盖 8 类攻击场景，每类 >= 2

## Level 3（合成对抗）

- 来自生产 bug，只增不删

## Ground Truth 字段

见 `tests/regression/schemas/collusion_expected.py`

| 字段 | 类型 | 说明 |
|------|------|------|
| pair_id | str | doc_a_id:doc_b_id |
| expected_overall_risk | high/medium/low/none/inconclusive | 专家标注 |
| expected_d1_score ~ d8_score | 0-1 | 逐维度 |
| expected_d1_evidence_level ~ ... | first/second/third | 逐维度 |
| expected_verified_findings | list[dict] | 每条 finding 的 page/quote 可验证性 |
| expected_failure_state | 五状态枚举 | |
| is_false_positive_candidate | bool | 容易误报的样本 |
| expert_note | str | |
