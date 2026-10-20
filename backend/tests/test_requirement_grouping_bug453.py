""" 防御测试: 招标要求确认页展示分组。

映射真实回归(项目 1474):
- _split_compound_requirements 把一条命中多子面的长条款拆成多行(同分类+同描述+同来源页,
 仅 sub_requirements/subject_scope 不同)，确认页因此出现肉眼无差别的重复卡片。
- 这些行不能在持久化/合并层去重(会回收 的分面检索)，必须在展示层折叠。

护栏关注:
1. 同分类+同描述的多行折叠为 1 个代表项，member_ids 收齐全部底层行 id。
2. 来源页码取并集并升序，便于"逐页核对"。
3. 否决项: 任一子面为否决项则整组为否决项(否则人工确认会漏判)。
4. confirmed: 全部子面已确认才算整组已确认。
5. 不同描述/不同分类绝不跨组合并。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://user:pass@localhost:5432/testdb",
)
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.models.requirement import Requirement, RequirementCategory  # noqa: E402
from app.services.requirement_grouping import group_requirements_for_display  # noqa: E402


def _make_row(
    *,
    id: int,
    category: RequirementCategory,
    description: str,
    sub: list[str],
    pages: list[int],
    is_mandatory: int = 0,
    confirmed: int = 0,
    merged_count: int | None = None,
) -> Requirement:
    import json

    row = Requirement(
        project_id=1,
        document_id=1,
        category=category,
        description=description,
        is_mandatory=is_mandatory,
        source_page=(pages[0] if pages else None),
        source_pages=json.dumps(pages),
        keywords=json.dumps([]),
        sub_requirements=json.dumps(sub),
        merged_count=merged_count,
        confirmed=confirmed,
    )
    row.id = id
    return row


_STAFF_DESC = (
    "人员安全培训与资质要求：乙方应对作业人员进行安全生产教育培训，"
    "使其具备相应的安全意识和技能；特种作业人员应具备相应的资格证书。"
)


def test_compound_facets_collapse_into_single_item() -> None:
    """同分类+同描述的 3 个复合拆分子面折叠为 1 个代表项。"""
    rows = [
        _make_row(id=14990, category=RequirementCategory.STAFF, description=_STAFF_DESC,
                  sub=["personnel_cert"], pages=[35]),
        _make_row(id=14991, category=RequirementCategory.STAFF, description=_STAFF_DESC,
                  sub=["personnel_aux"], pages=[35]),
        _make_row(id=14992, category=RequirementCategory.STAFF, description=_STAFF_DESC,
                  sub=["personnel_mgmt"], pages=[35]),
    ]

    items = group_requirements_for_display(rows)

    assert len(items) == 1
    item = items[0]
    assert item["member_ids"] == [14990, 14991, 14992]
    assert item["id"] == 14990
    assert item["description"] == _STAFF_DESC
    # merged_count 反映组内行数, 触发前端"多处合并"提示
    assert item["merged_count"] == 3
    # 子面并集保留, 供需要时溯源
    assert set(item["sub_requirements"]) == {"personnel_cert", "personnel_aux", "personnel_mgmt"}


def test_source_pages_unioned_and_sorted() -> None:
    """来源页码跨子面取并集并升序。"""
    rows = [
        _make_row(id=2, category=RequirementCategory.QUAL, description="同一资质条款",
                  sub=["enterprise_qual"], pages=[47, 7]),
        _make_row(id=1, category=RequirementCategory.QUAL, description="同一资质条款",
                  sub=["qual_system"], pages=[7, 12]),
    ]

    items = group_requirements_for_display(rows)

    assert len(items) == 1
    # 代表项取最小 id
    assert items[0]["id"] == 1
    assert items[0]["source_pages"] == [7, 12, 47]
    assert items[0]["source_page"] == 7


def test_group_mandatory_if_any_member_mandatory() -> None:
    """任一子面为否决项, 整组即否决项, 防止人工确认漏判。"""
    rows = [
        _make_row(id=1, category=RequirementCategory.STAFF, description="人员条款",
                  sub=["a"], pages=[1], is_mandatory=0),
        _make_row(id=2, category=RequirementCategory.STAFF, description="人员条款",
                  sub=["b"], pages=[1], is_mandatory=1),
    ]

    items = group_requirements_for_display(rows)

    assert len(items) == 1
    assert items[0]["is_mandatory"] is True


def test_group_confirmed_only_when_all_members_confirmed() -> None:
    rows = [
        _make_row(id=1, category=RequirementCategory.COMM, description="商务条款",
                  sub=["x"], pages=[1], confirmed=1),
        _make_row(id=2, category=RequirementCategory.COMM, description="商务条款",
                  sub=["y"], pages=[1], confirmed=0),
    ]

    items = group_requirements_for_display(rows)

    assert len(items) == 1
    assert items[0]["confirmed"] is False


def test_distinct_clauses_never_merged() -> None:
    """不同描述或不同分类绝不跨组合并。"""
    rows = [
        _make_row(id=1, category=RequirementCategory.STAFF, description="人员条款A",
                  sub=["a"], pages=[1]),
        _make_row(id=2, category=RequirementCategory.STAFF, description="人员条款B",
                  sub=["b"], pages=[2]),
        _make_row(id=3, category=RequirementCategory.QUAL, description="人员条款A",
                  sub=["c"], pages=[3]),
    ]

    items = group_requirements_for_display(rows)

    # 描述不同 / 分类不同 → 3 个独立组, 单成员无折叠
    assert len(items) == 3
    assert all(len(item["member_ids"]) == 1 for item in items)


def test_single_row_passthrough_preserves_merged_count() -> None:
    """单行(无可折叠)应原样透传, 不虚增 merged_count。"""
    rows = [
        _make_row(id=5, category=RequirementCategory.PERF, description="唯一业绩条款",
                  sub=["enterprise_perf"], pages=[48], merged_count=2),
    ]

    items = group_requirements_for_display(rows)

    assert len(items) == 1
    assert items[0]["member_ids"] == [5]
    # 单组不覆盖原 merged_count(其反映的是真实 provenance 合并)
    assert items[0]["merged_count"] == 2
