from app.schemas.requirement import ExtractedRequirement

import pytest

pytestmark = pytest.mark.scoring


def _req(
    description: str,
    *,
    mandatory: bool = False,
    kind: str = "normal",
    key_review: bool = False,
    score_weight: float | None = None,
    page: int = 1,
    keyword: str = "k",
) -> ExtractedRequirement:
    return ExtractedRequirement(
        category="TECH",
        description=description,
        is_mandatory=mandatory,
        keywords=[keyword],
        source_pages=[page],
        requirement_kind=kind,
        is_key_review=key_review,
        score_weight=score_weight,
    )


def test_protected_premerge_never_compresses_mandatory_duplicates():
    from app.core.extractor.tender_extractor_parts.protected_premerge import (
        compress_low_risk_duplicate_requirements,
    )

    items = [
        _req("投标人须承诺不得转包", mandatory=True, page=1),
        _req("投标人须承诺不得转包", mandatory=True, page=2),
    ]

    assert len(compress_low_risk_duplicate_requirements(items)) == 2


def test_protected_premerge_never_compresses_scoring_duplicates():
    from app.core.extractor.tender_extractor_parts.protected_premerge import (
        compress_low_risk_duplicate_requirements,
    )

    items = [
        _req("技术方案评分标准", kind="scoring_item", score_weight=10, page=1),
        _req("技术方案评分标准", kind="scoring_item", score_weight=10, page=2),
    ]

    assert len(compress_low_risk_duplicate_requirements(items)) == 2


def test_protected_premerge_never_compresses_high_risk_terms():
    from app.core.extractor.tender_extractor_parts.protected_premerge import (
        compress_low_risk_duplicate_requirements,
    )

    items = [
        _req("评分办法以技术方案完整性为准", page=1),
        _req("评分办法以技术方案完整性为准", page=2),
    ]

    assert len(compress_low_risk_duplicate_requirements(items)) == 2


def test_protected_premerge_compresses_low_risk_exact_duplicates():
    from app.core.extractor.tender_extractor_parts.protected_premerge import (
        compress_low_risk_duplicate_requirements,
    )

    items = [
        _req("项目背景说明供投标人参考", page=1, keyword="背景"),
        _req("项目背景说明供投标人参考", page=2, keyword="参考"),
    ]

    result = compress_low_risk_duplicate_requirements(items)

    assert len(result) == 1
    assert result[0].source_pages == [1, 2]
    assert result[0].keywords == ["背景", "参考"]
    assert result[0].merged_count == 2
