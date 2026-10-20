""": 绑定持久化策略单元测试。

覆盖 prepare_binding_rematch 的三种删除口径与 persist_rematched_bindings 的
保留人工决策、跳过重复候选、 写入守卫。全部使用轻量 stub，不连真实 DB。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql as pg

from app.core.arq_runtime import ProjectCancelledError
from app.core.writer.material_matching.binding_persistence import (
    BindingPersistenceDeps,
    persist_rematched_bindings,
    prepare_binding_rematch,
)
from app.models.writing_binding import BindingType


def _compile_where(stmt) -> str:
    wc = getattr(stmt, "whereclause", None)
    if wc is None:
        return ""
    return str(wc.compile(dialect=pg.dialect()))


def _capture_db() -> tuple[SimpleNamespace, list]:
    captured: list = []

    async def _execute(stmt, *_a, **_k):
        table_name = getattr(getattr(stmt, "table", None), "name", "")
        if table_name == "writing_bindings":
            captured.append(stmt)
        return SimpleNamespace(rowcount=3)

    db = SimpleNamespace(execute=AsyncMock(side_effect=_execute), commit=AsyncMock())
    return db, captured


@pytest.mark.asyncio
async def test_prepare_default_only_deletes_suggested() -> None:
    db, captured = _capture_db()

    await prepare_binding_rematch(db, 100, reset_reviewed_bindings=False, explicit_retry=False)

    assert len(captured) == 1
    where_sql = _compile_where(captured[0])
    assert "status" in where_sql
    assert "!=" not in where_sql  # 等值 status == suggested，保留 confirmed 与 rejected
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_prepare_explicit_retry_keeps_confirmed() -> None:
    db, captured = _capture_db()

    await prepare_binding_rematch(db, 100, explicit_retry=True)

    where_sql = _compile_where(captured[0])
    assert "status" in where_sql
    assert "!=" in where_sql  # status != confirmed


@pytest.mark.asyncio
async def test_prepare_reset_deletes_all_without_status_filter() -> None:
    db, captured = _capture_db()

    await prepare_binding_rematch(db, 100, reset_reviewed_bindings=True)

    where_sql = _compile_where(captured[0])
    assert "status" not in where_sql  # 仅 project_id 条件，删除全部


def _deps(cancelled: bool = False) -> BindingPersistenceDeps:
    return BindingPersistenceDeps(
        is_project_cancelled=lambda _pid: cancelled,
        auto_confirm_threshold=0.85,
    )


def _persist_db(preserved_rows: list, project_exists: bool = True) -> SimpleNamespace:
    project_result = MagicMock()
    project_result.scalar_one_or_none = MagicMock(return_value=999 if project_exists else None)
    preserved_result = MagicMock()
    preserved_result.scalars.return_value.all.return_value = preserved_rows
    return SimpleNamespace(
        execute=AsyncMock(side_effect=[project_result, preserved_result]),
        add=MagicMock(),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )


def _preserved_row(*, binding_type, material_id=None, clause_id=None, outline_node_id=None):
    return SimpleNamespace(
        binding_type=binding_type,
        material_id=material_id,
        clause_id=clause_id,
        outline_node_id=outline_node_id,
    )


@pytest.mark.asyncio
async def test_persist_skips_candidate_duplicating_preserved_rejected_edge() -> None:
    # 存活一条被拒绝的 (material 10 -> node 6473) 边；重算又提出同一 pair，必须跳过，
    # 避免被拒绝的错误边随重匹配复活或产生重复边。
    preserved = [
        _preserved_row(
            binding_type=BindingType.material_to_node,
            material_id=10,
            outline_node_id=6473,
        )
    ]
    db = _persist_db(preserved)
    candidates = [
        {
            "binding_type": BindingType.material_to_node,
            "clause_id": None,
            "outline_node_id": 6473,
            "material_id": 10,
            "confidence": 0.7,
            "source": "auto",
            "reason": "重算再次提出被拒绝的错误边",
        },
        {
            "binding_type": BindingType.material_to_node,
            "clause_id": None,
            "outline_node_id": 6481,
            "material_id": 10,
            "confidence": 0.75,
            "source": "auto",
            "reason": "正确章节候选",
        },
    ]

    saved = await persist_rematched_bindings(db, 999, candidates, _deps())

    saved_nodes = {b.outline_node_id for b in saved}
    assert saved_nodes == {6481}
    assert db.add.call_count == 1


@pytest.mark.asyncio
async def test_persist_empty_candidates_commit_without_insert() -> None:
    db = _persist_db([])
    saved = await persist_rematched_bindings(db, 999, [], _deps())
    assert saved == []
    db.add.assert_not_called()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_persist_raises_on_cancel_tombstone_before_db() -> None:
    db = _persist_db([])
    with pytest.raises(ProjectCancelledError):
        await persist_rematched_bindings(db, 999, [], _deps(cancelled=True))
    db.execute.assert_not_called()
