""" RED: resolve_material_label 边界场景覆盖

测试覆盖:
1. unknown subtype + 身份证 structured/caption → 应恢复 "身份证" (当前 RED: 返回 needs_review/None)
2. unknown subtype + ISO structured/caption + 文件名三体系 → 应形成 "ISO认证" (当前 RED: 返回 needs_review/None)
3. 模糊 unknown 无其他信号 → 应保持 needs_review/unknown (当前应 GREEN)
4. 税务/发票冲突 → 应保持 needs_review (当前应 GREEN，回归守卫)
"""

import json
from types import SimpleNamespace

from app.core.writer.label_resolver import resolve_material_label
from app.models.writing_material_image import ImageType


def _img(
    *,
    image_type: ImageType = ImageType.personnel_cert,
    business_subtype: str = "",
    caption: str | None = None,
    structured_fields: dict | str | None = None,
) -> SimpleNamespace:
    sf = (
        json.dumps(structured_fields, ensure_ascii=False)
        if isinstance(structured_fields, dict)
        else structured_fields
    )
    return SimpleNamespace(
        image_type=image_type,
        business_subtype=business_subtype,
        caption=caption,
        structured_fields=sf,
    )


def test_unknown_subtype_with_id_card_structured_and_caption_should_resolve_to_shenfenzheng() -> None:
    """ RED: business_subtype=unknown，但 structured_fields 与 caption 均含身份证关键词。
 期望：resolve_material_label 应从 structured/caption 推断出 resolved_label="身份证"。
 当前行为：uncertain_subtype_seen=True 导致跳过 structured/caption 处理，返回 needs_review/None。
 """
    images = [
        _img(
            image_type=ImageType.personnel_cert,
            business_subtype="unknown",
            caption="居民身份证",
            structured_fields={"证件类型": "居民身份证", "姓名": '<PERSON_A>'},
        )
    ]
    result = resolve_material_label(None, None, images)

    # RED: 当前实现因 unknown subtype 导致 skip，此断言应失败。
    # 修复后应通过: structured+caption 双重信号应恢复 resolved_label="身份证"。
    assert result.resolved_label == "身份证", (
        f"期望 resolved_label='身份证'，实际得到 '{result.resolved_label}'，"
        f"conflict_status='{result.conflict_status}'"
    )


def test_unknown_subtype_with_iso_three_source_consensus_should_resolve() -> None:
    """ RED: business_subtype=unknown，但 structured/caption 含 ISO 认证关键词，且文件名标签也指向 ISO。
 期望：三体系信号一致时应形成可用的 resolved_label（"ISO认证" 或 "三体系认证"）。
 当前行为：uncertain_subtype_seen=True 强制降级为 needs_review/None。
 """
    images = [
        _img(
            image_type=ImageType.qualification,
            business_subtype="unknown",
            caption="ISO9001质量管理体系认证",
            structured_fields={"资质名称": "ISO9001质量管理体系认证证书", "发证机关": "认证机构"},
        )
    ]
    result = resolve_material_label(
        "ISO认证",
        None,
        images,
    )

    # RED: 当前实现返回 needs_review/None，此断言应失败。
    # 修复后应通过: 文件名标签 + structured + caption 三体系一致应产出可用 resolved_label。
    assert result.resolved_label is not None, (
        f"期望 resolved_label 非 None，实际得到 None，conflict_status='{result.conflict_status}'"
    )
    assert result.resolved_label in {"ISO认证", "三体系认证"}, (
        f"期望 resolved_label 为 ISO 或三体系认证类，实际得到 '{result.resolved_label}'"
    )


def test_pure_unknown_subtype_without_other_signals_stays_needs_review() -> None:
    """回归守卫 (GREEN): 仅有 unknown subtype，无 structured/caption/文件名信号。
    期望：conflict_status='needs_review'，resolved_label=None。
    """
    images = [
        _img(
            image_type=ImageType.other,
            business_subtype="unknown",
            caption=None,
            structured_fields=None,
        )
    ]
    result = resolve_material_label(None, None, images)

    assert result.conflict_status == "needs_review", (
        f"期望 conflict_status='needs_review'，实际得到 '{result.conflict_status}'"
    )
    assert result.resolved_label is None, (
        f"期望 resolved_label=None，实际得到 '{result.resolved_label}'"
    )


def test_tax_invoice_conflict_stays_needs_review_no_regression() -> None:
    """回归守卫 (GREEN): 文件名=税务登记证，内容识别=发票，高风险冲突对。
    期望：不自动流转 resolved_label，保持 conflict_status='needs_review'。
    """
    images = [
        _img(
            image_type=ImageType.license,
            business_subtype="invoice_receipt",
            caption="发票查验明细",
            structured_fields={"发票号码": "123456", "价税合计": "1000.00"},
        )
    ]
    result = resolve_material_label("税务登记证", None, images)

    assert result.conflict_status == "needs_review", (
        f"期望高风险冲突 needs_review，实际得到 '{result.conflict_status}'"
    )
    assert result.resolved_label is None, (
        f"期望 resolved_label=None（高风险冲突不自动流转），实际得到 '{result.resolved_label}'"
    )
