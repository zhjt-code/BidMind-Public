"""PNG 确定性渲染器测试（步骤2）。"""

from pathlib import Path


def _flowchart_ir():
    from app.core.writer.visuals.ir_contracts import (
        FlowchartEdge,
        FlowchartIR,
        FlowchartNode,
    )

    nodes = [
        FlowchartNode(node_id="n1", label="确定项目经理"),
        FlowchartNode(node_id="n2", label="配置专职安全员"),
        FlowchartNode(node_id="n3", label="明确岗位职责"),
    ]
    return FlowchartIR(
        diagram_type="flowchart",
        title="施工管理组织流程图",
        nodes=nodes,
        edges=[
            FlowchartEdge(source="n1", target="n2"),
            FlowchartEdge(source="n2", target="n3"),
        ],
        content_sha256="0" * 64,
    )


def _gantt_ir():
    from app.core.writer.visuals.ir_contracts import GanttBar, GanttIR

    return GanttIR(
        diagram_type="gantt",
        title="实施进度甘特图",
        bars=[
            GanttBar(label="基础施工阶段", start_day=0, duration_days=30),
            GanttBar(label="设备安装调试阶段", start_day=30, duration_days=14),
        ],
        content_sha256="1" * 64,
    )


def test_render_flowchart_png(tmp_path: Path) -> None:
    from PIL import Image

    from app.core.writer.visuals.png_renderer import render_diagram_png

    out_path = tmp_path / "flowchart.png"
    result = render_diagram_png(_flowchart_ir(), out_path)
    assert result == out_path
    assert out_path.exists()
    with Image.open(out_path) as image:
        assert image.width > 100
        assert image.height > 100


def test_render_gantt_png(tmp_path: Path) -> None:
    from PIL import Image

    from app.core.writer.visuals.png_renderer import render_diagram_png

    out_path = tmp_path / "gantt.png"
    result = render_diagram_png(_gantt_ir(), out_path)
    assert result == out_path
    assert out_path.exists()
    with Image.open(out_path) as image:
        assert image.width > 100
        assert image.height > 100


def _flowchart_ir_n(node_count: int):
    from app.core.writer.visuals.ir_contracts import (
        FlowchartEdge,
        FlowchartIR,
        FlowchartNode,
    )

    nodes = [
        FlowchartNode(node_id=f"n{index}", label=f"步骤{index}")
        for index in range(1, node_count + 1)
    ]
    return FlowchartIR(
        diagram_type="flowchart",
        title="十二步实施流程图",
        nodes=nodes,
        edges=[
            FlowchartEdge(source=f"n{index}", target=f"n{index + 1}")
            for index in range(1, node_count)
        ],
        content_sha256="2" * 64,
    )


def test_flowchart_wraps_after_six_nodes() -> None:
    from app.core.writer.visuals.png_renderer import _layout_flowchart
    from app.core.writer.visuals.render_geometry import Box, path_hits_box_interior, route_between_boxes

    centers_six, *_rest = _layout_flowchart(6)
    assert all(x == 5.0 for x, _y in centers_six)

    centers_twelve, *_rest, box_w, box_h = _layout_flowchart(12)
    xs = {round(x, 2) for x, _y in centers_twelve}
    ys = {round(y, 2) for _x, y in centers_twelve}
    assert len(xs) == 4
    assert len(ys) == 3
    boxes = [Box(cx=cx, cy=cy, width=box_w, height=box_h) for cx, cy in centers_twelve]
    wrap_path = route_between_boxes(boxes[3], boxes[4])
    for index, box in enumerate(boxes):
        assert not path_hits_box_interior(wrap_path, box), f"wrap cuts n{index + 1}"


def test_twelve_node_flowchart_fits_a4_at_flowchart_width(tmp_path: Path) -> None:
    from PIL import Image

    from app.core.writer.visuals.page_fit import (
        MAX_DIAGRAM_HEIGHT_MM,
        fit_diagram_size_mm,
    )
    from app.core.writer.visuals.png_renderer import render_diagram_png

    out_path = tmp_path / "flowchart12.png"
    render_diagram_png(_flowchart_ir_n(12), out_path)
    with Image.open(out_path) as image:
        native_height_mm = 130 * (image.height / image.width)
        fitted_w, fitted_h = fit_diagram_size_mm(130, image.width, image.height)
    assert native_height_mm < MAX_DIAGRAM_HEIGHT_MM
    assert fitted_h <= MAX_DIAGRAM_HEIGHT_MM
    assert fitted_w == 130
