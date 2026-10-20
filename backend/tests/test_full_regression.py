"""P0-P3 全链路回归测试

用 trial data 文件夹中的实际素材文件名,
端到端验证: 文件名分类 -> 标签决议 -> 匹配器门禁 -> 结构化字段白名单 -> 渲染器收口
"""
import sys
import json
import inspect
sys.path.insert(0, ".")

from app.core.writer.filename_classifier import classify_by_filename
from app.core.writer.label_resolver import resolve_material_label, LabelResolution
from app.core.writer.image_subtype_contract import normalize_business_subtype, validate_business_subtype
from app.core.writer.section_composer import SectionComposer
from app.core.writer.doc_renderer import DocRenderer
from app.models.writing_material_image import ImageType
from app.models.writing_material import DocumentBusinessType


class MockImage:
    def __init__(self, image_type=None, caption="", structured_fields=None, business_subtype=""):
        self.image_type = image_type
        self.caption = caption
        self.structured_fields = structured_fields
        self.business_subtype = business_subtype


TRIAL_FILES = [
    "三体系确认证书.pdf",
    "公司简介.docx",
    "其它素材.pdf",
    "博士学位证.jpg",
    "招标文件.pdf",
    "法人身份证.jpg",
]

def run(name, condition, results):
    ok = bool(condition)
    results.append((name, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    return ok


def run_full_regression() -> bool:
    results = []

    # ===== P0: 文件名分类器 =====
    print("\n--- P0: filename_classifier ---")
    run("三体系确认证书 -> qualification_cert",
        classify_by_filename("三体系确认证书.pdf")[0] == DocumentBusinessType.qualification_cert, results)
    run("公司简介 -> company_profile",
        classify_by_filename("公司简介.docx")[0] == DocumentBusinessType.company_profile, results)
    run("其它素材 -> None",
        classify_by_filename("其它素材.pdf")[0] is None, results)
    run("博士学位证 -> personnel_info",
        classify_by_filename("博士学位证.jpg")[0] == DocumentBusinessType.personnel_info, results)
    run("招标文件 -> None (非素材)",
        classify_by_filename("招标文件.pdf")[0] is None, results)
    run("法人身份证 -> identity_document",
        classify_by_filename("法人身份证.jpg")[0] == DocumentBusinessType.identity_document, results)

    # ===== P0: 标签决议器 =====
    print("\n--- P0: label_resolver ---")
    img_lic = MockImage(
        ImageType.license,
        "营业执照",
        json.dumps({"证件类型": "营业执照"}, ensure_ascii=False),
    )
    r1 = resolve_material_label("法人身份证", None, [img_lic])
    run("高风险冲突降级: filename=法人身份证 + VL=营业执照 -> needs_review",
        r1.conflict_status == "needs_review" and r1.resolved_label is None, results)

    r2 = resolve_material_label("公司简介", "公司简介\n本公司成立于2010年...", [])
    run("一致性: filename=公司简介 + text=公司简介 -> matched",
        r2.conflict_status == "matched" and r2.resolved_label == "公司简介", results)

    r3 = resolve_material_label("学位证书", None, [])
    run("仅文件名: -> uncertain",
        r3.conflict_status == "uncertain" and r3.source == "filename" and r3.resolved_label is None, results)

    r4 = resolve_material_label(None, None, [])
    run("无信号: -> all None",
        r4.resolved_label is None and r4.conflict_status is None, results)

    r5 = resolve_material_label(None, None, [MockImage(ImageType.license, "税务登记证", business_subtype="tax_registration")])
    run("business_subtype 优先细化税务登记证",
        r5.content_label == "税务登记证" and r5.resolved_label == "税务登记证", results)
    r6 = resolve_material_label(None, None, [MockImage(ImageType.license, "发票查验明细", business_subtype="needs_review")])
    run("证据不足或冲突时不得自动产出 resolved_label",
        r6.conflict_status == "needs_review" and r6.resolved_label is None, results)
    r7 = resolve_material_label(
        None,
        None,
        [MockImage(
            ImageType.license,
            "税务登记证",
            json.dumps({"证件类型": "税务登记证"}, ensure_ascii=False),
            "needs_review",
        )],
    )
    run("needs_review 子类不会被 caption/structured 再次抬升",
        r7.conflict_status == "needs_review" and r7.resolved_label is None, results)

    # ===== P0: 模型与Prompt集成 =====
    print("\n--- P0: model + prompt integration ---")
    from app.models.writing_material import WritingMaterial
    from app.models.writing_material_image import WritingMaterialImage
    run("WritingMaterial.resolved_label 字段存在",
        hasattr(WritingMaterial, "resolved_label"), results)
    run("WritingMaterial.file_name_label 字段存在",
        hasattr(WritingMaterial, "file_name_label"), results)
    run("WritingMaterialImage.business_subtype 字段存在",
        hasattr(WritingMaterialImage, "business_subtype"), results)
    run("DocumentBusinessType 有 12 个枚举值",
        len(DocumentBusinessType) == 12, results)

    from app.core.writer.prompts.material_tag import get_material_tag_prompt
    _, usr = get_material_tag_prompt("test", "image", "test", "test", resolved_label="营业执照")
    run("Prompt 包含 resolved_label",
        "营业执照" in usr and "系统预判" in usr, results)
    run("subtype alias 可归一化",
        normalize_business_subtype("TaxRegistration") == "tax_registration", results)
    subtype_check, _ = validate_business_subtype(
        "tax_registration",
        vl_text="发票查验明细 开票日期 2023-06-06 价税合计 560.31",
        caption="图1: 发票查验明细",
    )
    run("invoice/tax 高混淆对会被验真器打回 needs_review",
        subtype_check == "needs_review", results)
    subtype_check2, _ = validate_business_subtype(
        "authorization_letter",
        vl_text="授权委托书",
        caption="图1: 授权委托书",
    )
    run("未配置验真规则的白名单子类不会直接放行",
        subtype_check2 == "needs_review", results)

    # ===== P1: 匹配器类型门禁 (后由 material_matching.type_gate 承担) =====
    # 旧 _label_forbidden_for_node 关键词表已随五路瀑布删除。此处只断言新门禁的
    # 确定性行为(证件跨大类冲突 block / 同类或不可归类 allow / 未验证图片 review)。
    # 旧表中"发票↛税务登记证/营业执照↛人员配置表/公司简介↛设备清单"等对，因章节
    # 标题不可归类到特定证件大类或属内容类，转由 LLM 句对裁决 + 章节 rejects 保障，
    # 不在确定性层断言(见 tests/writer/test_material_matching_golden.py)。
    print("\n--- P1: material_matching type gate ---")
    from types import SimpleNamespace

    from app.core.writer.material_matching.contracts import ChapterDemandProfile
    from app.core.writer.material_matching.material_taxonomy import (
        business_type_for_label,
        same_label_group,
    )
    from app.core.writer.material_matching.profiles import build_material_profiles
    from app.core.writer.material_matching.type_gate import evaluate_pair_gate

    def _profile(resolved_label, material_type="text", label_conflict="matched"):
        mat = SimpleNamespace(
            id=1,
            title=resolved_label or "素材",
            display_title=None,
            material_type=material_type,
            resolved_label=resolved_label,
            label_conflict=label_conflict,
            metadata_tags=None,
        )
        return build_material_profiles([mat])[0]

    def _chapter(title):
        return ChapterDemandProfile(node_id=1, title=title)

    run("发票 BLOCKED for 营业执照章节",
        evaluate_pair_gate(_profile("发票"), _chapter("营业执照副本")).decision == "block", results)
    run("开户许可证 BLOCKED for 身份证章节",
        evaluate_pair_gate(_profile("开户许可证"), _chapter("身份证明")).decision == "block", results)
    run("身份证 BLOCKED for 营业执照章节",
        evaluate_pair_gate(_profile("身份证"), _chapter("营业执照")).decision == "block", results)
    run("三体系认证 BLOCKED for 开户许可证章节 ",
        evaluate_pair_gate(_profile("三体系认证"), _chapter("开户许可证")).decision == "block", results)
    run("法人身份证 ALLOWED for 法定代表人章节",
        evaluate_pair_gate(_profile("法人身份证"), _chapter("法定代表人身份证明")).decision == "allow", results)
    run("营业执照 ALLOWED for 资质章节",
        evaluate_pair_gate(_profile("营业执照"), _chapter("企业资质证明")).decision == "allow", results)

    run("学位证书 可归类到 personnel_info 大类",
        business_type_for_label("学位证书") == DocumentBusinessType.personnel_info, results)
    run("三体系确认证书与ISO认证同义归组",
        same_label_group("三体系确认证书", "ISO认证"), results)
    run("身份证与法人身份证不归并 (主体不同)",
        not same_label_group("身份证", "法人身份证"), results)

    run("needs_review 图片素材门禁降级为 review (不得直接放行)",
        evaluate_pair_gate(
            _profile("营业执照", material_type="image", label_conflict="needs_review"),
            _chapter("营业执照副本"),
        ).decision == "review", results)

    # ===== P2: 结构化字段白名单 =====
    print("\n--- P2: autofill whitelist ---")
    sc = SectionComposer
    run("license OK for 营业执照章节",
        sc._is_autofill_compatible("license", "营业执照副本"), results)
    run("license BLOCKED for 身份证章节",
        not sc._is_autofill_compatible("license", "法定代表人身份证明"), results)
    run("personnel_cert OK for 法定代表人",
        sc._is_autofill_compatible("personnel_cert", "法定代表人身份证明"), results)
    run("personnel_cert BLOCKED for 营业执照",
        not sc._is_autofill_compatible("personnel_cert", "营业执照"), results)
    run("other type 无白名单限制",
        sc._is_autofill_compatible("other", "任意章节"), results)

    # ===== P3: 渲染器收口 =====
    print("\n--- P3: renderer consolidation ---")
    source = inspect.getsource(DocRenderer._replace_all_image_placeholders)
    run("渲染器不做全局补位 (no find_best_image)",
        "find_best_image_for_placeholder" not in source, results)
    run("渲染器标记为待人工确认",
        "图片待人工确认" in source, results)
    insert_source = inspect.getsource(DocRenderer._insert_figure_images)
    run("_insert_figure_images 支持 start_para_index",
        "start_para_index" in insert_source, results)
    run("_insert_figure_images 支持 current_section_title",
        "current_section_title" in insert_source, results)
    run("精确ID插入方法仍存在",
        hasattr(DocRenderer, "_insert_figure_images"), results)

    print("\n" + "=" * 60)
    total = len(results)
    passed = sum(1 for _, ok in results if ok)
    failed = total - passed
    if failed == 0:
        print(f"FULL REGRESSION (P0-P3): ALL {total} TESTS PASSED")
    else:
        print(f"FULL REGRESSION (P0-P3): {failed}/{total} TESTS FAILED")
        for name, ok in results:
            if not ok:
                print(f"  FAILED: {name}")
    return failed == 0


def test_full_regression() -> None:
    assert run_full_regression()


if __name__ == "__main__":
    sys.exit(0 if run_full_regression() else 1)
