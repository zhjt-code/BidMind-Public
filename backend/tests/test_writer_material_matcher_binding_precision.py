"""素材匹配绑定持久化与边级决策回归。

说明: 素材->章节匹配自 起由 material_matching 管线承担，旧五路瀑布
(标题直连/resolved_label模式表/传递推导/向量兜底)的方法与常量已物理删除。
原先断言这些内部实现的用例已迁移，其保护意图由以下测试承接:
- tests/writer/test_material_matching_golden.py (Project 1559 expected/forbidden)
- tests/writer/test_material_matching_pipeline.py (端到端 mock)
- tests/writer/test_material_type_gate.py (证件跨大类硬门禁)
- tests/writer/test_material_decision_policy.py (决策收敛/画像门禁/每素材上限)
- tests/test_full_regression.py P1 段 (类型门禁行为等价断言)

本文件保留: _save_bindings 持久化守卫 与边级拒绝隔离。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.writer.material_matcher import MaterialMatcher
from app.models.writing_binding import BindingStatus, BindingType
from app.services.writing.binding_service import BindingService


@pytest.mark.asyncio
async def test_save_bindings_guards_parent_row_and_preserves_reviewed_edges() -> None:
    # _save_bindings 不再删除任何 confirmed/rejected 边；写入前
    # 先查 projects 父行(守卫)，再查存活人工决策以跳过重复候选。
    # db.execute 现在被 await 两次(父行检查 + 存活人工决策查询)，均为只读 SELECT。
    project_result = MagicMock()
    project_result.scalar_one_or_none = MagicMock(return_value=100)  # 父行存在
    preserved_result = MagicMock()
    preserved_result.scalars.return_value.all.return_value = []  # 无存活人工决策
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[project_result, preserved_result]),
        add=lambda _obj: None,
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    matcher = MaterialMatcher(db=db, llm_client=SimpleNamespace())

    saved = await matcher._save_bindings(
        100,
        [
            {
                "binding_type": BindingType.material_to_node,
                "clause_id": None,
                "outline_node_id": 2,
                "material_id": 10,
                "confidence": 0.98,
                "source": "auto",
                "reason": "素材标题与章节标题字面对齐",
            }
        ],
    )

    # 父行检查 + 存活人工决策查询 = 2 次 await；无破坏性 delete
    assert db.execute.await_count == 2
    db.commit.assert_awaited_once()
    assert len(saved) == 1


@pytest.mark.asyncio
async def test_rejecting_material_binding_only_updates_target_edge_without_sibling_delete() -> None:
    # GREEN: 拒绝绑定仅更新目标边状态，不执行 sibling auto-suggested delete。
    # 允许缓存失效所需的只读查询，但不得删除同素材的其他绑定。
    rejected_binding = SimpleNamespace(
        id=1,
        project_id=100,
        material_id=10,
        binding_type=BindingType.material_to_node,
        status=BindingStatus.suggested,
    )
    artifact_result = MagicMock()
    artifact_result.scalars.return_value.first.return_value = None
    db = SimpleNamespace(
        get=AsyncMock(return_value=rejected_binding),
        execute=AsyncMock(return_value=artifact_result),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )

    async def _resolve_project(*_args, **_kwargs):
        return SimpleNamespace(id=100)

    service = BindingService(db=db, project_resolver=_resolve_project)

    updated = await service.update_binding(
        100,
        1,
        SimpleNamespace(),
        {"status": BindingStatus.rejected},
    )

    assert updated.status == BindingStatus.rejected
    # 回归保护：拒绝操作不得触发 sibling delete。
    db.execute.assert_awaited_once()
    assert str(db.execute.await_args.args[0]).lstrip().startswith("SELECT ")
    db.commit.assert_awaited_once()
