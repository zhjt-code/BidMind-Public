"""P0 回归测试: 文件名分类器 + 标签决议器

用 trial data 文件夹中的实际文件名验证分类和决议逻辑。
"""
import sys
import json
sys.path.insert(0, ".")

from app.core.writer.filename_classifier import classify_by_filename
from app.core.writer.label_resolver import resolve_material_label, LabelResolution
from app.core.writer.image_subtype_contract import normalize_business_subtype, validate_business_subtype
from app.models.writing_material_image import ImageType


class MockImage:
    """模拟 WritingMaterialImage ORM 对象"""
    def __init__(self, image_type=None, caption="", structured_fields=None, business_subtype=""):
        self.image_type = image_type
        self.caption = caption
        self.structured_fields = structured_fields
        self.business_subtype = business_subtype


def case_filename_classifier():
    """验证 trial data 中所有文件名的分类结果"""
    print("=== Test 1: filename_classifier ===")
    cases = [
        ("三体系确认证书.pdf", "qualification_cert"),
        ("公司简介.docx",     "company_profile"),
        ("其它素材.pdf",      "None"),
        ("博士学位证.jpg",    "personnel_info"),
        ("招标文件.pdf",      "None"),
        ("法人身份证.jpg",    "identity_document"),
    ]
    passed = 0
    for filename, expected_type in cases:
        biz_type, label = classify_by_filename(filename)
        actual = biz_type.value if biz_type else "None"
        ok = actual == expected_type
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {filename} -> type={actual}, label={label}")
        if ok:
            passed += 1
    total = len(cases)
    print(f"  Result: {passed}/{total} passed\n")
    return passed == total


def case_label_resolver_conflict():
    """高风险冲突应降级阻断，不再直接产出 resolved_label"""
    print("=== Test 2: label_resolver - conflict detection ===")
    img = MockImage(
        image_type=ImageType.license,
        caption="营业执照",
        structured_fields=json.dumps({"证件类型": "营业执照"}, ensure_ascii=False),
    )
    r = resolve_material_label(
        file_name_label="法人身份证",
        material_content=None,
        images=[img],
    )
    ok = (
        r.resolved_label is None
        and r.content_label == "营业执照"
        and r.conflict_status == "needs_review"
        and r.source == "conflict"
    )
    print(f"  [{('PASS' if ok else 'FAIL')}] resolved={r.resolved_label}, "
          f"content={r.content_label}, conflict={r.conflict_status}, source={r.source}")
    print("  Expected: resolved=None, conflict=needs_review, source=conflict\n")
    return ok


def case_label_resolver_matched():
    """文件名=公司简介 且正文含公司简介 -> matched"""
    print("=== Test 3: label_resolver - matched ===")
    r = resolve_material_label(
        file_name_label="公司简介",
        material_content="公司简介\n本公司成立于2010年,注册资金5000万元...",
        images=[],
    )
    ok = r.resolved_label == "公司简介" and r.conflict_status == "matched"
    print(f"  [{('PASS' if ok else 'FAIL')}] resolved={r.resolved_label}, conflict={r.conflict_status}")
    print(f"  Expected: resolved=公司简介, conflict=matched\n")
    return ok


def case_label_resolver_uncertain():
    """仅有文件名标签,无内容信号 -> uncertain"""
    print("=== Test 4: label_resolver - uncertain (filename only) ===")
    r = resolve_material_label(
        file_name_label="学位证书",
        material_content=None,
        images=[],
    )
    ok = r.resolved_label is None and r.conflict_status == "uncertain" and r.source == "filename"
    print(f"  [{('PASS' if ok else 'FAIL')}] resolved={r.resolved_label}, conflict={r.conflict_status}, source={r.source}")
    print("  Expected: resolved=None, conflict=uncertain, source=filename\n")
    return ok


def case_label_resolver_no_signal():
    """无任何信号 -> 全部None"""
    print("=== Test 5: label_resolver - no signal ===")
    r = resolve_material_label(
        file_name_label=None,
        material_content=None,
        images=[],
    )
    ok = r.resolved_label is None and r.conflict_status is None and r.source is None
    print(f"  [{('PASS' if ok else 'FAIL')}] resolved={r.resolved_label}, conflict={r.conflict_status}")
    print(f"  Expected: all None\n")
    return ok


def case_label_resolver_vl_only():
    """无文件名且仅单源图片信号时，不应直接自动放行"""
    print("=== Test 6: label_resolver - VL only, no filename ===")
    img = MockImage(
        image_type=ImageType.qualification,
        caption="质量管理体系认证证书",
    )
    r = resolve_material_label(
        file_name_label=None,
        material_content=None,
        images=[img],
    )
    ok = r.resolved_label is None and r.conflict_status == "needs_review" and r.source == "vl"
    print(f"  [{('PASS' if ok else 'FAIL')}] resolved={r.resolved_label}, conflict={r.conflict_status}, source={r.source}")
    print("  Expected: resolved=None, conflict=needs_review, source=vl\n")
    return ok


def case_label_resolver_identity_refinement():
    """VL识别身份证 -> content_label应细化为身份证而非泛化"""
    print("=== Test 7: label_resolver - identity refinement ===")
    img = MockImage(
        image_type=ImageType.personnel_cert,
        caption="居民身份证正面",
        structured_fields=json.dumps({"证件类型": "居民身份证", "姓名": '<PERSON_A>'}, ensure_ascii=False),
    )
    r = resolve_material_label(
        file_name_label="法人身份证",
        material_content=None,
        images=[img],
    )
    ok = r.content_label == "身份证" and r.conflict_status == "conflicted"
    print(f"  [{('PASS' if ok else 'FAIL')}] content_label={r.content_label}, resolved={r.resolved_label}, conflict={r.conflict_status}")
    print(f"  Expected: content_label=身份证, conflict=conflicted (法人身份证 != 身份证)\n")
    return ok


def case_label_resolver_business_subtype_refinement():
    """business_subtype 应优先于粗粒度 image_type，识别税务登记证"""
    print("=== Test 8: label_resolver - business subtype refinement ===")
    img = MockImage(
        image_type=ImageType.license,
        caption="税务登记证",
        business_subtype="tax_registration",
    )
    r = resolve_material_label(
        file_name_label=None,
        material_content=None,
        images=[img],
    )
    ok = r.content_label == "税务登记证" and r.resolved_label == "税务登记证"
    print(f"  [{'PASS' if ok else 'FAIL'}] content_label={r.content_label}, resolved={r.resolved_label}")
    print("  Expected: content_label=税务登记证, resolved=税务登记证\n")
    return ok


def case_subtype_contract_unknown_and_validation():
    """无效子类归一化为 unknown，税务/发票混淆走 needs_review"""
    print("=== Test 9: subtype contract ===")
    ok1 = normalize_business_subtype("TaxRegistration") == "tax_registration"
    ok2 = normalize_business_subtype("totally_invalid") == "unknown"
    subtype, _reasons = validate_business_subtype(
        "tax_registration",
        vl_text="发票查验明细 开票日期 2023-06-06 价税合计 560.31",
        caption="图1: 发票查验明细",
    )
    ok3 = subtype == "needs_review"
    print(f"  [{'PASS' if ok1 else 'FAIL'}] alias normalized to tax_registration: {ok1}")
    print(f"  [{'PASS' if ok2 else 'FAIL'}] invalid subtype downgraded to unknown: {ok2}")
    print(f"  [{'PASS' if ok3 else 'FAIL'}] invoice-vs-tax contradiction -> needs_review: {ok3}")
    return ok1 and ok2 and ok3


def case_label_resolver_needs_review_subtype_must_not_be_repromoted():
    """同一次分类结果若已降为 needs_review，不得再用 caption/structured 回抬 resolved_label"""
    print("=== Test 10: label_resolver - no re-promote after needs_review ===")
    img = MockImage(
        image_type=ImageType.license,
        caption="税务登记证",
        structured_fields=json.dumps({"证件类型": "税务登记证"}, ensure_ascii=False),
        business_subtype="needs_review",
    )
    r = resolve_material_label(
        file_name_label=None,
        material_content=None,
        images=[img],
    )
    ok = r.resolved_label is None and r.conflict_status == "needs_review"
    print(f"  [{'PASS' if ok else 'FAIL'}] resolved={r.resolved_label}, conflict={r.conflict_status}")
    print("  Expected: resolved=None, conflict=needs_review\n")
    return ok


def case_service_import():
    """验证 writing_service.py 能正常导入,新方法存在"""
    print("=== Test 11: service import check ===")
    try:
        from app.services.writing_service import WritingService
        ok1 = hasattr(WritingService, "_resolve_material_labels")
        print(f"  [{'PASS' if ok1 else 'FAIL'}] WritingService._resolve_material_labels exists: {ok1}")

        from app.models.writing_material import DocumentBusinessType, WritingMaterial
        ok2 = hasattr(WritingMaterial, "resolved_label")
        ok3 = hasattr(WritingMaterial, "file_name_label")
        print(f"  [{'PASS' if ok2 else 'FAIL'}] WritingMaterial.resolved_label exists: {ok2}")
        print(f"  [{'PASS' if ok3 else 'FAIL'}] WritingMaterial.file_name_label exists: {ok3}")

        ok4 = len(DocumentBusinessType) == 12
        print(f"  [{'PASS' if ok4 else 'FAIL'}] DocumentBusinessType has 12 values: {len(DocumentBusinessType)}")

        from app.models import DocumentBusinessType as DBT_Export
        ok5 = DBT_Export is DocumentBusinessType
        print(f"  [{'PASS' if ok5 else 'FAIL'}] DocumentBusinessType exported from models.__init__")

        return ok1 and ok2 and ok3 and ok4 and ok5
    except Exception as e:
        print(f"  [FAIL] Import error: {e}")
        return False


def case_tagger_prompt():
    """验证 material_tag prompt 接受 resolved_label 参数"""
    print("\n=== Test 12: tagger prompt accepts resolved_label ===")
    try:
        from app.core.writer.prompts.material_tag import get_material_tag_prompt
        sys_msg, usr_msg = get_material_tag_prompt(
            title="法人身份证.jpg",
            material_type="image",
            content_preview="[图片内容VL识别结果]\n营业执照 统一社会信用代码...",
            outline_tree="- 资质文件\n  - 营业执照\n  - 法定代表人身份证明",
            resolved_label="营业执照",
        )
        ok = "营业执照" in usr_msg and "系统预判" in usr_msg
        print(f"  [{'PASS' if ok else 'FAIL'}] resolved_label '营业执照' present in prompt: {ok}")
        if not ok:
            print(f"  usr_msg snippet: ...{usr_msg[-300:]}")
        return ok
    except Exception as e:
        print(f"  [FAIL] Error: {e}")
        return False


def test_p0_regression_suite() -> None:
    assert case_filename_classifier()
    assert case_label_resolver_conflict()
    assert case_label_resolver_matched()
    assert case_label_resolver_uncertain()
    assert case_label_resolver_no_signal()
    assert case_label_resolver_vl_only()
    assert case_label_resolver_identity_refinement()
    assert case_label_resolver_business_subtype_refinement()
    assert case_subtype_contract_unknown_and_validation()
    assert case_label_resolver_needs_review_subtype_must_not_be_repromoted()
    assert case_service_import()
    assert case_tagger_prompt()


if __name__ == "__main__":
    results = [
        case_filename_classifier(),
        case_label_resolver_conflict(),
        case_label_resolver_matched(),
        case_label_resolver_uncertain(),
        case_label_resolver_no_signal(),
        case_label_resolver_vl_only(),
        case_label_resolver_identity_refinement(),
        case_label_resolver_business_subtype_refinement(),
        case_subtype_contract_unknown_and_validation(),
        case_label_resolver_needs_review_subtype_must_not_be_repromoted(),
        case_service_import(),
        case_tagger_prompt(),
    ]

    total = len(results)
    passed = sum(results)
    failed = total - passed
    print("=" * 60)
    if failed == 0:
        print(f"P0 REGRESSION: ALL {total} TESTS PASSED")
    else:
        print(f"P0 REGRESSION: {failed}/{total} TESTS FAILED")
    sys.exit(0 if failed == 0 else 1)
