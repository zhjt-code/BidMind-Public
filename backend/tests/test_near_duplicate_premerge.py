from app.core.extractor.tender_extractor_parts.near_duplicate_premerge import (
    collapse_near_duplicates,
)
from app.schemas.requirement import ExtractedRequirement


def _req(
    description: str,
    *,
    category: str = "TECH",
    is_mandatory: bool = False,
    source_pages: list[int] | None = None,
) -> ExtractedRequirement:
    return ExtractedRequirement(
        category=category,
        description=description,
        is_mandatory=is_mandatory,
        keywords=["方案"],
        source_pages=source_pages or [1],
    )


def test_near_duplicate_premerge_collapses_low_risk_text_variants() -> None:
    requirements = [
        _req("投标人应提供完整的项目实施方案，内容包括进度安排和质量保障措施。", source_pages=[3]),
        _req("投标人应提供完整项目实施方案，内容包括进度安排和质量保障措施。", source_pages=[5]),
        _req("投标文件须按格式盖章。", category="FORMAT", source_pages=[9]),
    ]

    collapsed = collapse_near_duplicates(requirements, threshold=0.86)

    assert len(collapsed) == 2
    assert collapsed[0].source_pages == [3, 5]
    assert collapsed[0].merged_count == 2
    assert collapsed[1].description == "投标文件须按格式盖章。"


def test_near_duplicate_premerge_keeps_different_numbers_separate() -> None:
    requirements = [
        _req("供应商应提供近三年类似项目业绩3份。"),
        _req("供应商应提供近三年类似项目业绩5份。"),
    ]

    collapsed = collapse_near_duplicates(requirements, threshold=0.8)

    assert [item.description for item in collapsed] == [
        "供应商应提供近三年类似项目业绩3份。",
        "供应商应提供近三年类似项目业绩5份。",
    ]


def test_near_duplicate_premerge_does_not_auto_merge_protected_items() -> None:
    requirements = [
        _req("投标人必须提供完整的项目实施方案，内容包括进度安排和质量保障措施。"),
        _req("投标人必须提供完整项目实施方案，内容包括进度安排和质量保障措施。"),
    ]

    collapsed = collapse_near_duplicates(requirements, threshold=0.8)

    assert len(collapsed) == 2
