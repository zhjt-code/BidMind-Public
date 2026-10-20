""" 防御测试: 标书素材匹配在项目并发删除/墓碑命中时拒绝写入。

职责: 验证 MaterialMatcher._save_bindings 写入边界守卫与 match 阶段
 边界 cancellation 检查点的正确性, 以及 writing_match_task 异常分支
 区分 ProjectCancelledError 与业务失败的能力。
不做: 不验证 match 内部业务正确性、不验证 LLM 实际调用、不验证
 retry_service 状态机、不依赖真实数据库。

锁定的修复设计原则 (与 同源):
- 长异步任务跨多 commit 时必须在每个写入边界二次校验外键父行
- 项目取消必须走静默清理路径, 不污染 FAILED 终态
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.arq_runtime import ProjectCancelledError
from app.core.writer import material_matcher as material_matcher_mod
from app.core.writer.material_matcher import MaterialMatcher
from app.models.writing_binding import BindingStatus, BindingType


def _make_matcher(db_mock) -> MaterialMatcher:
    """构造仅注入 db 的 matcher, 跳过真实 LLM client 初始化。"""
    return MaterialMatcher(db=db_mock, llm_client=MagicMock(), project_id=999)


def _project_check_result(project_exists: bool) -> MagicMock:
    """模拟 select(Project.id) 的返回结果。"""
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(
        return_value=999 if project_exists else None
    )
    return result


class TestSaveBindingsTombstoneGuard:
    """_save_bindings 写入前必须先检查取消墓碑, 命中即抛错不查 DB。"""

    @pytest.mark.anyio
    async def test_tombstone_set_raises_before_db_query(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # 墓碑命中: is_project_cancelled 返回 True
        monkeypatch.setattr(
            material_matcher_mod, "is_project_cancelled", lambda pid: True
        )
        db_mock = MagicMock()
        db_mock.execute = AsyncMock(side_effect=AssertionError(
            "墓碑命中时不应再查 DB"
        ))
        matcher = _make_matcher(db_mock)

        with pytest.raises(ProjectCancelledError) as exc_info:
            await matcher._save_bindings(999, [])

        assert exc_info.value.project_id == 999
        # 墓碑命中是快路径, 不应触发任何 DB 操作
        db_mock.execute.assert_not_called()


class TestSaveBindingsParentRowGuard:
    """_save_bindings 必须在 INSERT 前查 projects 父行, 缺失即抛错。"""

    @pytest.mark.anyio
    async def test_project_missing_raises_before_insert(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # 墓碑未命中, 但 projects 表中实际无目标行 (并发 CASCADE 删除场景)
        monkeypatch.setattr(
            material_matcher_mod, "is_project_cancelled", lambda pid: False
        )
        db_mock = MagicMock()
        db_mock.execute = AsyncMock(return_value=_project_check_result(False))
        db_mock.add = MagicMock()
        db_mock.commit = AsyncMock()
        matcher = _make_matcher(db_mock)

        raw_bindings = [{
            "binding_type": BindingType.clause_to_node,
            "clause_id": 1,
            "outline_node_id": 2,
            "material_id": None,
            "confidence": 0.95,
            "source": "auto",
            "reason": "test",
            "scope": "exclusive",
        }]

        with pytest.raises(ProjectCancelledError) as exc_info:
            await matcher._save_bindings(999, raw_bindings)

        assert exc_info.value.project_id == 999
        # 父行校验失败, 后续 delete + add + commit 都不应执行
        db_mock.add.assert_not_called()
        db_mock.commit.assert_not_called()

    @pytest.mark.anyio
    async def test_project_exists_proceeds_to_insert(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """对照测试: 墓碑未命中且父行存在时不抛错, 进入正常 INSERT 流程。"""
        monkeypatch.setattr(
            material_matcher_mod, "is_project_cancelled", lambda pid: False
        )
        db_mock = MagicMock()
        # 第一次 execute = 父行检查 (返回 999);
        # 第二次 = 存活人工决策查询 (: 用于跳过重复候选, scalars.all=[])
        preserved_result = MagicMock()
        preserved_result.scalars.return_value.all.return_value = []
        db_mock.execute = AsyncMock(side_effect=[
            _project_check_result(True),
            preserved_result,
        ])
        db_mock.add = MagicMock()
        db_mock.commit = AsyncMock()
        db_mock.refresh = AsyncMock()
        matcher = _make_matcher(db_mock)

        raw_bindings = [{
            "binding_type": BindingType.clause_to_node,
            "clause_id": 1,
            "outline_node_id": 2,
            "material_id": None,
            "confidence": 0.95,
            "source": "auto",
            "reason": "test",
            "scope": "exclusive",
        }]

        saved = await matcher._save_bindings(999, raw_bindings)

        # 守卫通过, INSERT 正常执行
        assert len(saved) == 1
        db_mock.add.assert_called_once()
        db_mock.commit.assert_awaited_once()


class TestMatchPhaseBoundaryGuard:
    """match() 阶段边界必须主动检查墓碑, 命中即抛 ProjectCancelledError。"""

    @pytest.mark.anyio
    async def test_phase1_entry_raises_when_tombstone_set(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # 直接 patch material_matcher 模块内的 raise_if_project_cancelled 引用,
        # 比 patch 源头的 is_project_cancelled 更稳定 (raise_if 是 arq_runtime
        # 内部对 is_project_cancelled 的闭合调用, monkeypatch 不会穿透)
        def _raise_cancelled(pid: int) -> None:
            raise ProjectCancelledError(pid, detail="phase1 entry guard")

        monkeypatch.setattr(
            material_matcher_mod, "raise_if_project_cancelled", _raise_cancelled
        )

        db_mock = MagicMock()
        matcher = _make_matcher(db_mock)
        # mock 加载方法返回空数据, 不进入实际 LLM 调用
        matcher._load_confirmed_clauses = AsyncMock(return_value=[])
        matcher._load_outline_nodes = AsyncMock(return_value=[])
        matcher._load_materials = AsyncMock(return_value=[])

        with pytest.raises(ProjectCancelledError) as exc_info:
            await matcher.match(999)

        assert exc_info.value.project_id == 999


@pytest.fixture
def anyio_backend():
    """统一用 asyncio backend, 避免 trio 依赖。"""
    return "asyncio"
