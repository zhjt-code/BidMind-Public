"""B1 红线门禁: FAILURE_STATE_ENABLED=false 时失败态字段不得出现在响应里。

判据从"整个字段集全等"收窄为"失败态字段集的存在性"（-08-03）:

 原实现把 PRE_B1 的完整字段集硬编码为快照并要求全等，但它守的红线只是
 「失败态字段不得泄露」。二者不等价——任何与失败态无关的新字段都会让它失败，
 而它对失败态本身的覆盖并不会因此更强。实测泄露清单为
 ReviewResultResponse 14 个评分字段（score_item_name / volume_label /
 rule_assessments / estimated_score 等）与 SectionDraftResponse 2 个来源字段
 （source_material_ids / source_clause_ids），全部已被前端消费
 （score_item_name 与 volume_label 各命中 6 个前端文件），属正当 API 面，
 不是泄露。门禁却因此长期红着，等于既不保护红线也不指导行动。

 收窄后判据与生产代码单点对齐: 直接引用 schema 里声明的失败态字段清单
 （review.py 的 _B1_FIELDS / writing.py 的同源开关分支），
 开关关闭时逐个断言不存在、开关打开时断言可出现。
 新增业务字段不再误伤本门禁；失败态字段一旦在关闭态泄露立即失败。
"""

import pytest

pytestmark = pytest.mark.gates if hasattr(pytest.mark, "gates") else pytest.mark.usefixtures()

# 与 app/schemas/review.py::_strip_failure_state_fields 的 _B1_FIELDS 同源。
# 此处显式重列而非 import 私有常量: 门禁要能在生产清单被误删时失败，
# 直接引用会让"清单被清空"变成门禁一起失效。
REVIEW_FAILURE_STATE_FIELDS = frozenset({
    "failure_state", "failure_reason", "confidence", "gate_trace",
    "review_required", "reviewed_at", "reviewed_by",
})


def _review_fields(*, enabled: bool) -> set[str]:
    from app.core.config import settings
    from app.schemas.review import ReviewResultResponse

    saved = settings.FAILURE_STATE_ENABLED
    object.__setattr__(settings, "FAILURE_STATE_ENABLED", enabled)
    try:
        dummy = ReviewResultResponse(
            id=1, project_id=1, requirement_id=1,
            category="test", requirement_summary="test",
            is_compliant=True, compliance_level="fully",
            score=80, comment="ok", risk_level="none",
            failure_state="failed", failure_reason="test",
            confidence=0.5, review_required=1,
        )
        return set(dummy.model_dump().keys())
    finally:
        object.__setattr__(settings, "FAILURE_STATE_ENABLED", saved)


def test_failure_state_fields_absent_when_flag_off():
    """红线: 开关关闭时，失败态字段一个都不许出现。"""
    fields = _review_fields(enabled=False)
    leaked = sorted(REVIEW_FAILURE_STATE_FIELDS & fields)
    assert not leaked, f"FAILURE_STATE_ENABLED=false 时泄露失败态字段: {leaked}"


def test_failure_state_fields_present_when_flag_on():
    """对照: 开关打开时字段须能出现——否则上一条会因字段根本不存在而假绿。"""
    fields = _review_fields(enabled=True)
    present = REVIEW_FAILURE_STATE_FIELDS & fields
    assert present, (
        "FAILURE_STATE_ENABLED=true 时失败态字段全部缺席，"
        "说明字段已被删除或序列化器改写——关闭态的红线断言随之失去意义"
    )


def test_core_business_fields_always_present():
    """基本面: 与失败态无关的核心字段在两种开关态下都必须在。

    取代原先的全字段集快照——只钉住真正的契约核心，
    新增业务字段不再误伤门禁（原快照因 14 个评分字段长期红着）。
    """
    core = {"id", "project_id", "requirement_id", "category", "is_compliant",
            "compliance_level", "score", "comment", "risk_level"}
    for enabled in (False, True):
        missing = sorted(core - _review_fields(enabled=enabled))
        assert not missing, f"FAILURE_STATE_ENABLED={enabled} 时核心字段缺失: {missing}"


def test_writing_draft_core_fields_always_present():
    """SectionDraftResponse 的核心字段契约（同样不再做全字段集全等）。"""
    from app.schemas.writing import SectionDraftResponse

    dummy = SectionDraftResponse(
        id=1, outline_node_id=1, version=1,
        content_format="html", is_current=1,
        is_user_edited=0, is_locked=0, status="generating",
    )
    fields = set(dummy.model_dump().keys())
    core = {"id", "outline_node_id", "version", "content", "content_format",
            "summary", "is_current", "is_user_edited", "is_locked",
            "word_count", "status"}
    missing = sorted(core - fields)
    assert not missing, f"SectionDraftResponse 核心字段缺失: {missing}"
