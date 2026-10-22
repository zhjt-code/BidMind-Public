"""建筑向 network/wbs/zone_layout IR、闸门与 A4 适配。"""

from pathlib import Path

import pytest

from app.core.writer.visuals.construction_pack import ZONE_LAYOUT_CAPTION_SUFFIX
from app.core.writer.visuals.intent_gate import gate_named_diagram_intent
from app.core.writer.visuals.ir_builder import DiagramBuildError, build_diagram_ir
from app.core.writer.visuals.page_fit import MAX_DIAGRAM_HEIGHT_MM, fit_diagram_size_mm
from app.core.writer.visuals.png_renderer import render_diagram_png


def test_network_ir_png_and_critical_path_fit_a4(tmp_path: Path) -> None:
    from PIL import Image

    ir = build_diagram_ir({}, {
        "diagram_type": "network",
        "title": "施工进度网络图",
        "network_nodes": [
            {"node_id": "n1", "label": "准备", "duration_days": 10},
            {"node_id": "n2", "label": "基础", "duration_days": 30},
            {"node_id": "n3", "label": "主体", "duration_days": 60},
            {"node_id": "n4", "label": "验收", "duration_days": 15},
        ],
        "network_edges": [
            {"source": "n1", "target": "n2"},
            {"source": "n2", "target": "n3"},
            {"source": "n3", "target": "n4"},
        ],
    })
    assert ir.diagram_type == "network"
    assert len(ir.nodes) == 4
    out_path = tmp_path / "network.png"
    render_diagram_png(ir, out_path)
    with Image.open(out_path) as image:
        fitted_w, fitted_h = fit_diagram_size_mm(160, image.width, image.height)
    assert fitted_h <= MAX_DIAGRAM_HEIGHT_MM
    assert fitted_w <= 160


def test_network_cycle_is_rejected() -> None:
    with pytest.raises(DiagramBuildError) as exc:
        build_diagram_ir({}, {
            "diagram_type": "network",
            "title": "环路网络图",
            "network_nodes": [
                {"node_id": "n1", "label": "A", "duration_days": 5},
                {"node_id": "n2", "label": "B", "duration_days": 5},
                {"node_id": "n3", "label": "C", "duration_days": 5},
            ],
            "network_edges": [
                {"source": "n1", "target": "n2"},
                {"source": "n2", "target": "n3"},
                {"source": "n3", "target": "n1"},
            ],
        })
    assert exc.value.failure_code == "network_cycle"


def test_network_missing_duration_fails_gate() -> None:
    payload = {
        "network_nodes": [
            {"label": "准备", "duration_days": 10},
            {"label": "实施"},
            {"label": "验收", "duration_days": 5},
        ],
    }
    assert gate_named_diagram_intent("network", payload) is False


def test_wbs_ir_and_png(tmp_path: Path) -> None:
    from PIL import Image

    ir = build_diagram_ir({}, {
        "diagram_type": "wbs",
        "title": "工程分解结构",
        "wbs_root": "单位工程",
        "wbs_packages": [
            {"label": "基础工程", "items": ["土方", "垫层"]},
            {"label": "主体结构", "items": ["钢筋", "模板", "混凝土"]},
            {"label": "装饰工程", "items": ["抹灰"]},
        ],
    })
    assert ir.root == "单位工程"
    assert len(ir.packages) == 3
    out_path = tmp_path / "wbs.png"
    render_diagram_png(ir, out_path)
    assert Image.open(out_path).size[0] > 100


def test_zone_layout_forces_caption_and_filters_vocab(tmp_path: Path) -> None:
    from PIL import Image

    payload = {
        "title": "场地功能分区",
        "zones": ["办公区", "基坑支护区", "生活区", "材料堆场", "大门"],
    }
    assert gate_named_diagram_intent("zone_layout", payload) is True
    assert "基坑支护区" not in payload["zones"]
    ir = build_diagram_ir({}, {
        "diagram_type": "zone_layout",
        **payload,
    })
    assert ZONE_LAYOUT_CAPTION_SUFFIX in ir.title
    assert "基坑支护区" not in ir.zones
    out_path = tmp_path / "zones.png"
    render_diagram_png(ir, out_path)
    with Image.open(out_path) as image:
        fitted_w, fitted_h = fit_diagram_size_mm(150, image.width, image.height)
    assert fitted_h <= MAX_DIAGRAM_HEIGHT_MM
    assert fitted_w <= 150


def test_zone_layout_too_few_known_zones_fails_gate() -> None:
    payload = {"zones": ["办公区", "未知区"]}
    assert gate_named_diagram_intent("zone_layout", payload) is False
