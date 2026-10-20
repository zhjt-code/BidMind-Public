"""图片-章节亲和性规则测试"""

import os
import sys
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.writer.image_node_affinity import (
    filter_images_for_node,
    find_best_image_for_placeholder,
)
from app.models.writing_material_image import ImageType


def _make_img(
    img_id: int = 1,
    image_type: ImageType | None = None,
    caption: str = "",
    business_subtype: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=img_id,
        image_type=image_type,
        caption=caption,
        business_subtype=business_subtype,
        image_path="fake.jpg",
        width=800,
        height=600,
        image_index=0,
    )


# ==================== filter_images_for_node ====================


def test_filter_images_for_node_license_chapter() -> None:
    """'营业执照'章节只保留 license 类型图片"""
    images = [
        _make_img(1, ImageType.license, "图1: XX公司营业执照"),
        _make_img(2, ImageType.personnel_cert, '图2: <PERSON_A>身份证'),
        _make_img(3, ImageType.qualification, "图3: ISO9001认证"),
        _make_img(4, ImageType.other, "图4: 投标保证金发票"),
    ]
    result = filter_images_for_node(images, "营业执照、组织机构代码证、税务登记证（或三证合一证件）")
    assert len(result) == 1
    assert result[0].id == 1


def test_filter_images_for_node_personnel_chapter() -> None:
    """'身份证明'章节只保留 personnel_cert 类型图片"""
    images = [
        _make_img(1, ImageType.license, "图1: 营业执照"),
        _make_img(2, ImageType.personnel_cert, "图2: 法定代表人身份证"),
        _make_img(3, ImageType.other, "图3: 荣誉证书"),
    ]
    result = filter_images_for_node(images, "法定代表人身份证明")
    assert len(result) == 1
    assert result[0].id == 2


def test_filter_images_for_node_qualification_chapter() -> None:
    """'资质'章节只保留 qualification 类型图片"""
    images = [
        _make_img(1, ImageType.license, "图1: 营业执照"),
        _make_img(2, ImageType.qualification, "图2: 科研服务专业准入证"),
        _make_img(3, ImageType.other, "图3: 发票"),
    ]
    result = filter_images_for_node(images, "科研服务专业准入证")
    assert len(result) == 1
    assert result[0].id == 2


def test_filter_images_for_node_equipment_chapter() -> None:
    """'设备仪器'章节保留 nameplate 和 photo 类型"""
    images = [
        _make_img(1, ImageType.nameplate, "图1: 光谱仪铭牌"),
        _make_img(2, ImageType.photo, "图2: 实验室设备照片"),
        _make_img(3, ImageType.license, "图3: 营业执照"),
    ]
    result = filter_images_for_node(images, "设备仪器")
    ids = {img.id for img in result}
    assert 1 in ids
    assert 2 in ids
    assert 3 not in ids


def test_filter_images_for_node_no_match_returns_empty() -> None:
    """无匹配时返回空列表"""
    images = [
        _make_img(1, ImageType.license, "图1: 营业执照"),
    ]
    result = filter_images_for_node(images, "投标报价")
    assert result == []


def test_filter_images_for_node_empty_inputs() -> None:
    """空输入安全处理"""
    assert filter_images_for_node([], "营业执照") == []
    assert filter_images_for_node([_make_img(1)], "") == []


def test_filter_images_for_node_caption_fallback() -> None:
    """image_type 为 None 时通过 caption 关键词兜底匹配"""
    images = [
        _make_img(1, None, "XX公司营业执照副本"),
    ]
    result = filter_images_for_node(images, "营业执照")
    assert len(result) == 1
    assert result[0].id == 1


def test_filter_images_for_node_keeps_confirmed_contract_scans_for_performance_chapter() -> None:
    """: 合同/业绩章节应保留 other+unknown 的合同扫描附件。"""
    images = [
        _make_img(
            1,
            ImageType.other,
            "图1: 压裂液技术试验项目合同扫描件",
            business_subtype="unknown",
        ),
        _make_img(
            2,
            ImageType.other,
            "图2: 合同扫描件",
            business_subtype="needs_review",
        ),
        _make_img(
            3,
            ImageType.other,
            "图3: 发票查验明细",
            business_subtype="invoice_receipt",
        ),
    ]

    result = filter_images_for_node(images, "近三年井筒腐蚀类项目合同复印件")

    assert [img.id for img in result] == [1, 2]


def test_filter_images_for_node_business_subtype_beats_coarse_type() -> None:
    """细粒度 business_subtype 应优先命中税务登记章节"""
    images = [
        _make_img(
            1,
            ImageType.license,
            "图1: 税务登记证",
            business_subtype="tax_registration",
        ),
        _make_img(
            2,
            ImageType.other,
            "图2: 发票查验明细",
            business_subtype="invoice_receipt",
        ),
    ]
    result = filter_images_for_node(images, "税务登记证")
    assert [img.id for img in result] == [1]


def test_filter_images_for_node_business_subtype_miss_can_fallback_to_generic_type() -> None:
    """细粒度子类未命中时，泛标题章节仍可回退到粗类匹配"""
    images = [
        _make_img(
            1,
            ImageType.license,
            "图1: 营业执照副本",
            business_subtype="business_license",
        ),
    ]
    result = filter_images_for_node(images, "企业证件")
    assert [img.id for img in result] == [1]


def test_filter_images_for_node_personnel_team() -> None:
    """'团队人员配置'章节匹配 personnel_cert"""
    images = [
        _make_img(1, ImageType.personnel_cert, "项目经理证书"),
        _make_img(2, ImageType.license, "营业执照"),
    ]
    result = filter_images_for_node(images, "团队主要人员配置及资历")
    assert len(result) == 1
    assert result[0].id == 1


# ==================== find_best_image_for_placeholder ====================


def test_find_best_for_placeholder_type_match() -> None:
    """优先按 image_type 匹配"""
    candidates = [
        _make_img(1, ImageType.other, "发票"),
        _make_img(2, ImageType.license, "营业执照"),
        _make_img(3, ImageType.personnel_cert, "身份证"),
    ]
    best = find_best_image_for_placeholder(candidates, "营业执照")
    assert best is not None
    assert best.id == 2


def test_find_best_for_placeholder_no_match_returns_none() -> None:
    """无匹配返回 None，不强制塞入"""
    candidates = [
        _make_img(1, ImageType.other, "发票"),
        _make_img(2, ImageType.other, "合同书"),
    ]
    best = find_best_image_for_placeholder(candidates, "营业执照")
    assert best is None


def test_find_best_for_placeholder_caption_fallback() -> None:
    """无 type 匹配时通过 caption 关键词排序"""
    candidates = [
        _make_img(1, ImageType.other, "XX公司营业执照扫描件"),
    ]
    best = find_best_image_for_placeholder(candidates, "营业执照")
    assert best is not None
    assert best.id == 1


def test_find_best_for_placeholder_prefers_business_subtype() -> None:
    """细粒度 business_subtype 存在时优先于粗类命中"""
    candidates = [
        _make_img(1, ImageType.license, "营业执照", business_subtype="business_license"),
        _make_img(2, ImageType.license, "税务登记证", business_subtype="tax_registration"),
    ]
    best = find_best_image_for_placeholder(candidates, "税务登记证")
    assert best is not None
    assert best.id == 2


def test_find_best_for_placeholder_empty_inputs() -> None:
    """空输入安全处理"""
    assert find_best_image_for_placeholder([], "营业执照") is None
    assert find_best_image_for_placeholder([_make_img(1)], "") is None


# ==================== _calc_image_width ====================


def test_calc_image_width_with_dimensions() -> None:
    """有宽高信息时按 150DPI 换算"""
    from docx.shared import Mm

    from app.core.writer.doc_renderer import DocRenderer

    rec = _make_img(1, ImageType.license)
    rec.width = 2480
    rec.height = 3508
    width = DocRenderer._calc_image_width(rec)
    assert width >= Mm(120), "证照类至少120mm"
    assert width <= Mm(160), "不超过160mm上限"


def test_calc_image_width_zero_dimensions_cert() -> None:
    """宽高为0的证照类图片默认140mm"""
    from docx.shared import Mm

    from app.core.writer.doc_renderer import DocRenderer

    rec = _make_img(1, ImageType.license)
    rec.width = 0
    rec.height = 0
    width = DocRenderer._calc_image_width(rec)
    assert width == Mm(140)


def test_calc_image_width_zero_dimensions_other() -> None:
    """宽高为0的非证照图片默认120mm"""
    from docx.shared import Mm

    from app.core.writer.doc_renderer import DocRenderer

    rec = _make_img(1, ImageType.photo)
    rec.width = 0
    rec.height = 0
    width = DocRenderer._calc_image_width(rec)
    assert width == Mm(120)


def test_calc_image_width_small_image() -> None:
    """小图片不低于60mm下限"""
    from docx.shared import Mm

    from app.core.writer.doc_renderer import DocRenderer

    rec = _make_img(1, ImageType.other)
    rec.width = 100
    rec.height = 100
    width = DocRenderer._calc_image_width(rec)
    assert width >= Mm(60), "不低于60mm下限"


# ==================== is_exclusive_image (Word/预览共用去重判定) ====================


def test_is_exclusive_image_by_business_subtype() -> None:
    """独占型业务子类(证照/票据)应判定为独占"""
    from app.core.writer.image_node_affinity import is_exclusive_image

    assert is_exclusive_image(_make_img(business_subtype="business_license")) is True
    assert is_exclusive_image(_make_img(business_subtype="id_card")) is True
    # 荣誉证书可多处出现, 非独占
    assert is_exclusive_image(_make_img(business_subtype="honor_cert")) is False


def test_is_exclusive_image_fallback_to_image_type() -> None:
    """无 business_subtype 时按 image_type 粗类判定"""
    from app.core.writer.image_node_affinity import is_exclusive_image

    assert is_exclusive_image(_make_img(image_type=ImageType.license)) is True
    assert is_exclusive_image(_make_img(image_type=ImageType.personnel_cert)) is True
    assert is_exclusive_image(_make_img(image_type=ImageType.photo)) is False
    assert is_exclusive_image(_make_img(image_type=None)) is False


def test_doc_renderer_is_exclusive_image_delegates_consistently() -> None:
    """DocRenderer._is_exclusive_image 转发后与共享判定结果一致(防回归)"""
    from app.core.writer.doc_renderer import DocRenderer
    from app.core.writer.image_node_affinity import is_exclusive_image

    for rec in (
        _make_img(business_subtype="tax_registration"),
        _make_img(business_subtype="honor_cert"),
        _make_img(image_type=ImageType.qualification),
        _make_img(image_type=ImageType.diagram),
    ):
        assert DocRenderer._is_exclusive_image(rec) == is_exclusive_image(rec)


def test_filter_site_layout_for_plane_chapter() -> None:
    images = [
        _make_img(1, ImageType.diagram, "图1: 施工总平面", business_subtype="site_layout"),
        _make_img(2, ImageType.diagram, "图2: 工艺流程", business_subtype="process_diagram"),
    ]
    result = filter_images_for_node(images, "施工平面布置")
    assert [img.id for img in result] == [1]


def test_filter_section_drawing_for_support_chapter() -> None:
    images = [
        _make_img(1, ImageType.diagram, "图1: 支护剖面", business_subtype="section_drawing"),
        _make_img(2, ImageType.diagram, "图2: 组织机构", business_subtype="organization_diagram"),
    ]
    result = filter_images_for_node(images, "基坑支护专项方案")
    assert [img.id for img in result] == [1]


def test_filter_network_drawing_for_schedule_chapter() -> None:
    images = [
        _make_img(1, ImageType.diagram, "图1: 进度网络", business_subtype="network_drawing"),
        _make_img(2, ImageType.license, "图2: 营业执照"),
    ]
    result = filter_images_for_node(images, "施工进度计划")
    assert [img.id for img in result] == [1]


def test_construction_drawings_are_not_exclusive() -> None:
    from app.core.writer.image_node_affinity import is_exclusive_image

    assert is_exclusive_image(_make_img(business_subtype="site_layout")) is False
    assert is_exclusive_image(_make_img(business_subtype="section_drawing")) is False
    assert is_exclusive_image(_make_img(business_subtype="network_drawing")) is False
