"""标书生成/渲染 -- 绑定消费策略（单一真源）。

职责: 依据绑定类型与单边状态选择可进入生成或渲染的绑定。
不做: 不查询数据库、不加载素材、不推断语义相关性。

背景:
- 约束(clause)绑定沿用既有"组级人工审核"策略：一旦同组出现 confirmed/rejected，
 则该组只消费 confirmed；全为 suggested 时消费 suggested + confirmed（未审核即默认采纳）。
- 素材(material_to_node)绑定改为"逐边只消费 confirmed"：素材绑定始终以 suggested 落库、
 需人工确认，一条被 rejected 的错误边不得影响同节点其他 confirmed 边，未确认边也不进入消费。
- 生成侧与渲染侧必须共享本模块，避免两处各自实现导致语义分叉。
"""

from __future__ import annotations

from app.models.writing_binding import BindingStatus, WritingBinding


def status_to_str(status: object) -> str:
    """兼容枚举与字符串两种 status 表示。"""
    if hasattr(status, "value"):
        return str(getattr(status, "value"))
    return str(status)


def select_consumable_bindings(
    bindings: list[WritingBinding],
) -> list[WritingBinding]:
    """约束绑定组级消费策略（保持既有行为）。

    - 组内出现任一 confirmed/rejected（人工已开始审核）: 只消费 confirmed。
    - 组内全为 suggested（尚未审核）: 消费 suggested + confirmed。
    """
    if not bindings:
        return []
    status_values = {
        status_to_str(binding.status)
        for binding in bindings
        if binding.status is not None
    }
    manual_review_started = any(
        status in {BindingStatus.confirmed.value, BindingStatus.rejected.value}
        for status in status_values
    )
    if manual_review_started:
        allowed = {BindingStatus.confirmed.value}
    else:
        allowed = {
            BindingStatus.suggested.value,
            BindingStatus.confirmed.value,
        }
    return [
        binding for binding in bindings
        if status_to_str(binding.status) in allowed
    ]


def select_confirmed_material_bindings(
    bindings: list[WritingBinding],
) -> list[WritingBinding]:
    """素材绑定逐边消费策略：仅消费 confirmed。

 逐边独立判定，一条 rejected/suggested 边不影响同组其他 confirmed 边。
 """
    return [
        binding for binding in bindings
        if status_to_str(binding.status) == BindingStatus.confirmed.value
    ]


def collect_confirmed_material_ids(
    binding_groups: list[list[WritingBinding]],
) -> list[int]:
    """按组收集"已确认素材绑定"的 material_id，保序去重。"""
    material_ids: list[int] = []
    seen: set[int] = set()
    for group in binding_groups:
        for binding in select_confirmed_material_bindings(group):
            material_id = getattr(binding, "material_id", None)
            if material_id is None or material_id in seen:
                continue
            seen.add(material_id)
            material_ids.append(material_id)
    return material_ids


__all__ = [
    "status_to_str",
    "select_consumable_bindings",
    "select_confirmed_material_bindings",
    "collect_confirmed_material_ids",
]
