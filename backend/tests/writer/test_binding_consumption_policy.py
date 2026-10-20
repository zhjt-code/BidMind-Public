""": 绑定消费策略单元测试。

覆盖约束组级策略与素材逐边 confirmed-only 策略，重点验证"一条 rejected 边不影响
同节点其他 confirmed 边"，以及素材 id 保序去重。全部为纯内存函数测试，不连 DB。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.core.writer.binding_consumption_policy import (
    collect_confirmed_material_ids,
    select_confirmed_material_bindings,
    select_consumable_bindings,
    status_to_str,
)
from app.models.writing_binding import BindingStatus


def _b(status, *, material_id=None, clause_id=None):
    return SimpleNamespace(status=status, material_id=material_id, clause_id=clause_id)


def test_status_to_str_handles_enum_and_plain_string() -> None:
    assert status_to_str(BindingStatus.confirmed) == "confirmed"
    assert status_to_str("rejected") == "rejected"


# ── 约束组级策略（保持既有行为）─────────────────────────────

def test_clause_policy_empty_returns_empty() -> None:
    assert select_consumable_bindings([]) == []


def test_clause_policy_all_suggested_consumes_suggested() -> None:
    rows = [_b(BindingStatus.suggested, clause_id=1), _b(BindingStatus.suggested, clause_id=2)]
    assert len(select_consumable_bindings(rows)) == 2


def test_clause_policy_once_reviewed_only_confirmed() -> None:
    rows = [
        _b(BindingStatus.confirmed, clause_id=1),
        _b(BindingStatus.suggested, clause_id=2),
        _b(BindingStatus.rejected, clause_id=3),
    ]
    kept = select_consumable_bindings(rows)
    assert [b.clause_id for b in kept] == [1]


# ── 素材逐边 confirmed-only 策略──────────────────

def test_material_policy_empty_returns_empty() -> None:
    assert select_confirmed_material_bindings([]) == []


def test_material_policy_only_confirmed_edge_consumed() -> None:
    rows = [
        _b(BindingStatus.confirmed, material_id=10),
        _b(BindingStatus.suggested, material_id=11),
        _b(BindingStatus.rejected, material_id=12),
    ]
    kept = select_confirmed_material_bindings(rows)
    assert [b.material_id for b in kept] == [10]


def test_material_policy_rejected_edge_does_not_drop_sibling_confirmed() -> None:
    # 同节点跨素材: 一条 rejected(错误素材) 不得影响另一条 confirmed(正确素材)。
    rows = [
        _b(BindingStatus.rejected, material_id=691),   # 错误章节被拒绝
        _b(BindingStatus.confirmed, material_id=692),  # 正确素材确认
    ]
    kept = select_confirmed_material_bindings(rows)
    assert [b.material_id for b in kept] == [692]


def test_material_policy_suggested_only_not_consumed() -> None:
    rows = [_b(BindingStatus.suggested, material_id=10)]
    assert select_confirmed_material_bindings(rows) == []


def test_collect_confirmed_material_ids_orders_and_dedupes() -> None:
    group_a = [
        _b(BindingStatus.confirmed, material_id=5),
        _b(BindingStatus.rejected, material_id=6),
    ]
    group_b = [
        _b(BindingStatus.confirmed, material_id=5),  # 跨组重复
        _b(BindingStatus.confirmed, material_id=7),
        _b(BindingStatus.suggested, material_id=8),
    ]
    assert collect_confirmed_material_ids([group_a, group_b]) == [5, 7]


def test_collect_confirmed_material_ids_skips_none_material_id() -> None:
    group = [_b(BindingStatus.confirmed, material_id=None), _b(BindingStatus.confirmed, material_id=9)]
    assert collect_confirmed_material_ids([group]) == [9]
