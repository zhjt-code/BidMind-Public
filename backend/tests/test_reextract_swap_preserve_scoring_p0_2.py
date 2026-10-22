""" 防御测试：点击重新提取必须预清全部已提取条目。

职责: 锁定重提取预清不再保留任何 Requirement（含 scoring_item / 已确认 / 手工补录）。
不做: 不连真实 PostgreSQL、不执行后台任务、不调用 LLM。
"""

from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy.sql.dml import Delete

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

pytestmark = pytest.mark.scoring


class _RecordingDb:
    def __init__(self) -> None:
        self.statements: list[object] = []

    async def execute(self, stmt: object) -> None:
        self.statements.append(stmt)


def test_reextract_service_must_not_pre_delete_non_manual_scoring_items():
    """历史用例名保留：服务层仍不得直接套 JSON 过滤删评分项，必须委托 swap。"""
    from app.services.requirement_reextract_service import RequirementReextractService

    source = inspect.getsource(RequirementReextractService.reextract_from_parsed_documents)

    assert "non_manual_scoring_metadata_filter" not in source
    assert "clear_extracted_artifacts" in source


def test_swap_module_clear_extracted_artifacts_exists_and_includes_scoring_items():
    """swap 预清必须存在，且不得再排除 scoring_item。"""
    from app.services.requirement_reextract_swap import clear_extracted_artifacts

    source = inspect.getsource(clear_extracted_artifacts)
    assert '!= "scoring_item"' not in source
    assert "!= 'scoring_item'" not in source
    assert "delete(Requirement)" in source


@pytest.mark.asyncio
async def test_clear_extracted_artifacts_deletes_requirements_by_project_only() -> None:
    """点击重新提取时，Requirement 删除条件只能是 project_id，必须覆盖评分项。"""
    from app.services.requirement_reextract_swap import clear_extracted_artifacts

    db = _RecordingDb()
    await clear_extracted_artifacts(2417, db)

    requirement_deletes = [
        stmt
        for stmt in db.statements
        if isinstance(stmt, Delete) and str(stmt.table.name) == "requirements"
    ]
    assert len(requirement_deletes) == 1
    where_sql = str(requirement_deletes[0].whereclause)
    assert "project_id" in where_sql
    assert "scoring_item" not in where_sql
    assert "confirmed" not in where_sql
    assert "manual" not in where_sql


def test_reextract_service_delegates_artifact_clearing_to_swap_module():
    """reextract service 必须薄委托 swap 模块，不自行执行预删逻辑。"""
    from app.services.requirement_reextract_service import RequirementReextractService

    source = inspect.getsource(RequirementReextractService)
    assert "clear_extracted_artifacts" in source
    assert "count_existing_outputs" in source


def test_swap_module_count_existing_outputs_is_importable():
    """swap 模块必须导出 count_existing_outputs 供 service 调用。"""
    from app.services.requirement_reextract_swap import count_existing_outputs

    assert callable(count_existing_outputs)
