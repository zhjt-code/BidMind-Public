"""Stage B 护栏测试: 类型硬门禁。

覆盖:
- 跨大类证件冲突拦截(身份证->营业执照 / 体系认证->开户许可证)
- 同大类放行(资质证书->资质证书章节)，把精度区分留给召回+LLM
- 未验证图片降级 review
- 章节 rejects / 素材 inapplicable 否定信号 block
- 内容类素材(公司简介)不参与证件冲突
"""

from app.core.writer.material_matching.contracts import (
    ChapterDemandProfile,
    MaterialSemanticProfile,
)
from app.core.writer.material_matching.type_gate import evaluate_pair_gate


def _mat(**kwargs) -> MaterialSemanticProfile:
    base = dict(material_id=1, title="", material_type="text", label_verified=True)
    base.update(kwargs)
    return MaterialSemanticProfile(**base)


def _chap(**kwargs) -> ChapterDemandProfile:
    base = dict(node_id=1, title="")
    base.update(kwargs)
    return ChapterDemandProfile(**base)


def test_cross_category_certificate_conflict_blocked() -> None:
    identity = _mat(business_type="identity_document", resolved_label="身份证")
    assert evaluate_pair_gate(identity, _chap(title="营业执照")).decision == "block"

    cert = _mat(business_type="qualification_cert", resolved_label="三体系认证")
    assert evaluate_pair_gate(cert, _chap(title="开户许可证")).decision == "block"


def test_same_category_allowed_for_downstream_precision() -> None:
    cert = _mat(business_type="qualification_cert", resolved_label="资质证书")
    # 资质证书 vs 资质证书章节: 同大类，门禁放行，精度交给召回/LLM
    assert evaluate_pair_gate(cert, _chap(title="资质证书")).decision == "allow"
    assert evaluate_pair_gate(cert, _chap(title="企业资质认证")).decision == "allow"


def test_unverified_image_downgraded_to_review() -> None:
    image = _mat(
        material_type="image",
        business_type="qualification_cert",
        label_verified=False,
    )
    assert evaluate_pair_gate(image, _chap(title="企业资质认证")).decision == "review"


def test_chapter_rejects_blocks_material() -> None:
    material = _mat(resolved_label="投标保证金", title="投标保证金缴纳凭证")
    chapter = _chap(title="质量管理体系认证", rejects=["投标保证金", "开户许可证"])
    assert evaluate_pair_gate(material, chapter).decision == "block"


def test_material_inapplicable_blocks_chapter() -> None:
    material = _mat(resolved_label="职称证书", inapplicable_sections=["技术方案", "报价"])
    assert evaluate_pair_gate(material, _chap(title="技术方案")).decision == "block"


def test_content_type_material_not_treated_as_certificate() -> None:
    profile = _mat(business_type="company_profile", resolved_label="公司简介")
    # 内容类素材(公司简介)即使遇到证件类章节标题，也不做证件冲突 block
    assert evaluate_pair_gate(profile, _chap(title="营业执照")).decision == "allow"
