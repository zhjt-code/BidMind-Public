from tests.synthetic_data import synthetic_text

"""身份证图片布局计划器防御测试。"""

import json
from types import SimpleNamespace

from app.models.writing_material_image import ImageType


def _id_card(
    img_id: int,
    *,
    caption: str,
    vl_content: str = "",
    structured_fields: dict | None = None,
    material_id: int = 1,
    image_index: int = 0,
    business_subtype: str = "id_card",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=img_id,
        material_id=material_id,
        image_index=image_index,
        image_path=f"{img_id}.png",
        caption=caption,
        vl_content=vl_content,
        structured_fields=json.dumps(structured_fields or {}, ensure_ascii=False),
        image_type=ImageType.personnel_cert,
        business_subtype=business_subtype,
        file_hash=f"hash-{img_id}",
        width=1200,
        height=760,
    )


def test_layout_orders_legal_then_agent_and_front_before_back() -> None:
    from app.core.writer.identity_image_layout import build_identity_image_layout

    images = [
        _id_card(
            4,
            caption="委托代理人身份证反面",
            vl_content="签发机关 XX公安局 有效期限 2020-2030",
            image_index=3,
        ),
        _id_card(
            2,
            caption="法定代表人身份证反面",
            vl_content="签发机关 XX公安局 有效期限 2020-2030",
            image_index=1,
        ),
        _id_card(
            3,
            caption="委托代理人身份证正面",
            structured_fields={"姓名": '<PERSON_B>', "证件号码": synthetic_text('110101', '199001', '011234')},
            image_index=2,
        ),
        _id_card(
            1,
            caption="法定代表人身份证正面",
            structured_fields={"姓名": '<PERSON_A>', "证件号码": synthetic_text('110101', '198001', '011234')},
            image_index=0,
        ),
    ]

    layout = build_identity_image_layout(images)

    assert layout.ordered_image_ids == (1, 2, 3, 4)
    assert [(row.role, row.front.image_id, row.back.image_id) for row in layout.rows] == [
        ("legal_representative", 1, 2),
        ("agent", 3, 4),
    ]


def test_layout_ignores_non_identity_images() -> None:
    from app.core.writer.identity_image_layout import build_identity_image_layout

    images = [
        _id_card(
            7,
            caption="营业执照",
            business_subtype="business_license",
        ),
    ]

    layout = build_identity_image_layout(images)

    assert layout.rows == ()
    assert layout.ordered_image_ids == ()


def test_layout_infers_unknown_sides_from_stable_order() -> None:
    from app.core.writer.identity_image_layout import build_identity_image_layout

    images = [
        _id_card(12, caption="法定代表人身份证第2页", material_id=9, image_index=1),
        _id_card(11, caption="法定代表人身份证第1页", material_id=9, image_index=0),
    ]

    layout = build_identity_image_layout(images)

    assert layout.ordered_image_ids == (11, 12)
    assert len(layout.rows) == 1
    row = layout.rows[0]
    assert row.role == "legal_representative"
    assert row.front.image_id == 11
    assert row.back.image_id == 12
    assert "side_inferred_from_order" in row.front.review_reasons
    assert "side_inferred_from_order" in row.back.review_reasons
