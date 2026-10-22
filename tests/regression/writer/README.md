# 标书编写回归样本

公开数据规范见仓库根目录 `docs/test-data-policy.md`。

## Level 1（历史场景合成复刻）

- 目标规模：15 项目 x 每项目 ~80 章节
- 初版（B0.7）：3 项目
- 素材充足度分层：充足(>20 材料) 5 项目 / 一般(10-20) 6 项目 / 不足(<10) 4 项目

## Level 2（幻觉攻击）

- 每流程 >= 45 个，覆盖 8 类攻击场景，每类 >= 2

## Level 3（合成对抗）

- 来自生产 bug，只增不删

## Ground Truth 字段

见 `tests/regression/schemas/writer_expected.py`

| 字段 | 类型 | 说明 |
|------|------|------|
| outline_node_id | int | |
| expected_node_type | fixed_template/data_fill/free_writing | 理想分类 |
| expected_material_ids | list[int] | 应引用的素材 |
| expected_placeholder_density | float | 可接受的 [待补充] 密度（每千字） |
| expected_failure_state | 五状态枚举 | |
| has_fabrication_risk | bool | 高幻觉风险章节 |
| expert_note | str | |
