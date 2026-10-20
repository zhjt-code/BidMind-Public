import os
import sys
from types import SimpleNamespace
import json

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.writer.image_subtype_contract import (
    normalize_business_subtype,
    validate_business_subtype,
    UNKNOWN_SUBTYPE,
    NEEDS_REVIEW_SUBTYPE,
)
from app.core.writer.material_tagger import MaterialTagger


def test_normalize_business_subtype_invalid_to_unknown() -> None:
    assert normalize_business_subtype("totally_invalid_value") == UNKNOWN_SUBTYPE


def test_normalize_business_subtype_alias() -> None:
    assert normalize_business_subtype("TaxRegistration") == "tax_registration"


def test_validate_business_subtype_invoice_vs_tax_registration() -> None:
    subtype, reasons = validate_business_subtype(
        "tax_registration",
        vl_text="发票查验明细 开票日期 2023-06-06 价税合计 560.31",
        caption="图1: 发票查验明细",
    )
    assert subtype == NEEDS_REVIEW_SUBTYPE
    assert any("negative_only" in reason or "contradictory_evidence" in reason for reason in reasons)


def test_validate_business_subtype_tax_registration_positive() -> None:
    subtype, reasons = validate_business_subtype(
        "tax_registration",
        vl_text="税务登记证 税务登记号码 123 纳税人识别号 456",
        caption="图1: 税务登记证",
    )
    assert subtype == "tax_registration"
    assert any("validated" in reason for reason in reasons)


def test_validate_business_subtype_unknown_stays_unknown() -> None:
    subtype, reasons = validate_business_subtype(
        UNKNOWN_SUBTYPE,
        vl_text="图片模糊，无法判断",
    )
    assert subtype == UNKNOWN_SUBTYPE
    assert reasons == ["model_uncertain"]


def test_validate_business_subtype_without_rule_downgrades_to_needs_review() -> None:
    subtype, reasons = validate_business_subtype(
        "authorization_letter",
        vl_text="授权委托书",
        caption="图1: 授权委托书",
    )
    assert subtype == NEEDS_REVIEW_SUBTYPE
    assert reasons == ["no_validation_rule"]


def test_normalize_construction_drawing_subtypes() -> None:
    assert normalize_business_subtype("site_layout") == "site_layout"
    assert normalize_business_subtype("SiteLayout") == "site_layout"
    assert normalize_business_subtype("section_drawing") == "section_drawing"
    assert normalize_business_subtype("networkdrawing") == "network_drawing"


def test_validate_site_layout_positive() -> None:
    subtype, reasons = validate_business_subtype(
        "site_layout",
        vl_text="施工平面布置图 生活区 材料堆场 大门",
        caption="图1: 施工总平面布置",
    )
    assert subtype == "site_layout"
    assert any("validated" in reason for reason in reasons)


def test_validate_section_drawing_positive() -> None:
    subtype, reasons = validate_business_subtype(
        "section_drawing",
        vl_text="基坑支护剖面 放坡 锚杆",
        caption="图2: 支护剖面",
    )
    assert subtype == "section_drawing"
    assert any("validated" in reason for reason in reasons)


def test_validate_network_drawing_positive() -> None:
    subtype, reasons = validate_business_subtype(
        "network_drawing",
        vl_text="关键线路 网络图 紧前工作",
        caption="图3: 进度网络图",
    )
    assert subtype == "network_drawing"
    assert any("validated" in reason for reason in reasons)


class _FakeDb:
    async def commit(self) -> None:
        return None


def test_material_tagger_skips_unverified_image_labels() -> None:
    tagger = MaterialTagger(db=_FakeDb(), llm_client=object())
    material = SimpleNamespace(
        id=1,
        title="发票截图",
        content="发票查验明细",
        material_type="image",
        label_conflict="needs_review",
        metadata_tags=None,
    )

    import asyncio

    result = asyncio.run(tagger.tag(material, outline_tree="- 税务登记证"))
    assert json.loads(material.metadata_tags)["applicable_sections"] == []
    assert result.get("exclusive_section") is None
