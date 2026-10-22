"""建筑选图包：空间章禁算法图、分区词表、真图优先。"""

from app.core.writer.visuals.construction_pack import (
    ZONE_LAYOUT_CAPTION_SUFFIX,
    ensure_zone_layout_caption,
    extra_type_terms,
    is_spatial_drawing_chapter,
    normalize_zone_names,
    superseded_by_material,
)
from app.core.writer.visuals.eligibility_policy import filter_visual_intents
from app.core.writer.visuals.intent_templates import (
    FLOWCHART_TEMPLATES,
    LAYERED_TEMPLATES,
    flowchart_template_labels,
)


def test_spatial_terms_detected() -> None:
    assert is_spatial_drawing_chapter("基坑支护专项方案") is True
    assert is_spatial_drawing_chapter("脚手架搭设") is True
    assert is_spatial_drawing_chapter("BIM效果图") is True
    assert is_spatial_drawing_chapter("施工平面布置") is False
    assert is_spatial_drawing_chapter("施工工艺") is False


def test_zone_vocab_filters_unknown() -> None:
    assert normalize_zone_names(["办公区", "基坑支护区", "生活区", "办公区"]) == [
        "办公区",
        "生活区",
    ]


def test_zone_caption_forced_once() -> None:
    titled = ensure_zone_layout_caption("场地功能分区")
    assert ZONE_LAYOUT_CAPTION_SUFFIX in titled
    assert titled.count("非施工平面图") == 1
    again = ensure_zone_layout_caption(titled)
    assert again.count("非施工平面图") == 1


def test_material_supersede_zone_and_network() -> None:
    assert superseded_by_material("zone_layout", {"site_layout"}) is True
    assert superseded_by_material("network", {"network_drawing"}) is True
    assert superseded_by_material("zone_layout", {"process_diagram"}) is False
    assert superseded_by_material("wbs", {"site_layout"}) is False


def test_new_type_terms_present() -> None:
    assert "平面布置" in extra_type_terms("zone_layout")
    assert "关键线路" in extra_type_terms("network")
    assert "工作分解" in extra_type_terms("wbs")


def test_construction_templates_registered() -> None:
    assert flowchart_template_labels("three_inspection") == [
        "自检", "互检", "专检", "整改闭合",
    ]
    assert flowchart_template_labels("construction_process")[0] == "施工准备"
    assert "qhse" in LAYERED_TEMPLATES
    assert "construction_safety" in FLOWCHART_TEMPLATES or (
        "construction_safety" in LAYERED_TEMPLATES
    )


def test_filter_supersedes_zone_layout_when_site_layout_present() -> None:
    result = filter_visual_intents(
        {
            "visuals": [
                {
                    "visual_id": "vis_1",
                    "diagram_type": "zone_layout",
                    "title": "场地功能分区",
                    "purpose": "示意临设分区",
                }
            ],
        },
        node_title="施工平面布置",
        chapter_profile={"semantic_role": "现场平面布置"},
        material_subtypes={"site_layout"},
    )
    assert result.payload["visuals"] == []
    assert result.patches[0]["kind"] == "visual_superseded_by_material"


def test_filter_keeps_zone_layout_without_true_drawing() -> None:
    result = filter_visual_intents(
        {
            "visuals": [
                {
                    "visual_id": "vis_1",
                    "diagram_type": "zone_layout",
                    "title": "场地功能分区",
                    "purpose": "示意临设分区",
                }
            ],
        },
        node_title="施工平面布置",
        chapter_profile={"semantic_role": "现场平面布置"},
    )
    assert len(result.payload["visuals"]) == 1
