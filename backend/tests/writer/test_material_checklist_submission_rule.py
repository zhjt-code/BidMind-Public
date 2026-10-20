from types import SimpleNamespace

import pytest


def _clause(clause_id: int, sub_type: str, content: str):
    return SimpleNamespace(id=clause_id, sub_type=sub_type, content=content)


class _ChecklistGenerator:
    def __init__(self):
        self.loaded_sub_types: list[str] = []

    async def _load_clauses(self, _project_id: int, sub_type: str):
        self.loaded_sub_types.append(sub_type)
        if sub_type == "material_required":
            return []
        if sub_type == "submission_notice":
            return [_clause(1, sub_type, "逐页盖章")]
        if sub_type == "submission_rule":
            return [_clause(2, sub_type, "正本一份、副本三份")]
        return []

    async def _load_materials(self, _project_id: int):
        return []


from app.core.writer.outline_generator import OutlineGenerator


class _OutlineGeneratorForChecklist(_ChecklistGenerator, OutlineGenerator):
    pass


@pytest.mark.asyncio
async def test_material_checklist_includes_submission_rule_notices() -> None:
    """: submission_rule 应出现在素材页注意事项清单中。"""
    generator = _OutlineGeneratorForChecklist()

    checklist = await generator.generate_material_checklist(project_id=9)

    assert "submission_rule" in generator.loaded_sub_types
    notice_items = [item for item in checklist if item.category == "notice"]
    assert [item.clause_id for item in notice_items] == [1, 2]
