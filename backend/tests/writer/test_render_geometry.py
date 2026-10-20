""": 贴边正交路由与节点种类推断。"""

from __future__ import annotations

from app.core.writer.visuals.png_renderer import _layout_flowchart
from app.core.writer.visuals.render_geometry import (
    Box,
    infer_flowchart_kind,
    is_orthogonal,
    is_outside_near_edge,
    org_bus_paths,
    path_hits_box_interior,
    route_between_boxes,
)
from app.core.writer.visuals.styles import paint_for_kind, resolve_diagram_style


def _box(cx: float, cy: float, width: float, height: float) -> Box:
    return Box(cx=cx, cy=cy, width=width, height=height)


def test_infer_flowchart_kind_marks_start_end_and_question() -> None:
    labels = ["提取清算结果", "发现异常?", "归档闭环"]
    kinds = [
        infer_flowchart_kind(index, len(labels), label)
        for index, label in enumerate(labels)
    ]
    assert kinds == ["start", "decision", "end"]


def test_same_row_route_stays_on_left_right_edges() -> None:
    source = _box(1.0, 2.0, 2.0, 1.0)
    target = _box(4.0, 2.0, 2.0, 1.0)
    path = route_between_boxes(source, target)
    assert is_orthogonal(path)
    assert is_outside_near_edge(path[0], source)
    assert is_outside_near_edge(path[-1], target)
    assert path[0][0] > source.right
    assert path[-1][0] < target.left
    assert not path_hits_box_interior(path, source)
    assert not path_hits_box_interior(path, target)


def test_same_column_downward_route_uses_bottom_and_top_edges() -> None:
    source = _box(5.0, 7.5, 5.2, 1.1)
    target = _box(5.0, 5.5, 5.2, 1.1)
    path = route_between_boxes(source, target)
    assert is_orthogonal(path)
    assert path[0][0] == source.cx
    assert path[0][1] < source.bottom
    assert path[-1][0] == target.cx
    assert path[-1][1] > target.top
    assert not path_hits_box_interior(path, source)
    assert not path_hits_box_interior(path, target)


def test_same_column_upward_route_uses_top_and_bottom_edges() -> None:
    source = _box(5.0, 5.5, 5.2, 1.1)
    target = _box(5.0, 7.5, 5.2, 1.1)
    path = route_between_boxes(source, target)
    assert is_orthogonal(path)
    assert path[0][0] == source.cx
    assert path[0][1] > source.top
    assert path[-1][0] == target.cx
    assert path[-1][1] < target.bottom
    assert not path_hits_box_interior(path, source)
    assert not path_hits_box_interior(path, target)


def test_twelve_node_wrap_route_does_not_cut_through_row() -> None:
    centers, _fig_w, _fig_h, _xlim, _ylim, box_w, box_h = _layout_flowchart(12)
    boxes = [_box(cx, cy, box_w, box_h) for cx, cy in centers]
    wrap_path = route_between_boxes(boxes[3], boxes[4])
    assert is_orthogonal(wrap_path)
    assert is_outside_near_edge(wrap_path[0], boxes[3])
    assert is_outside_near_edge(wrap_path[-1], boxes[4])
    for index, box in enumerate(boxes):
        if index in {3, 4}:
            assert not path_hits_box_interior(wrap_path, box)
            continue
        assert not path_hits_box_interior(wrap_path, box), f"wrap cuts through n{index + 1}"


def test_cross_lane_route_uses_column_alley_not_centers() -> None:
    source = _box(2.4, 3.0, 1.3, 0.76)
    target = _box(4.2, 1.5, 1.3, 0.76)
    other = _box(2.4, 1.5, 1.3, 0.76)
    path = route_between_boxes(source, target)
    assert is_orthogonal(path)
    assert is_outside_near_edge(path[0], source)
    assert is_outside_near_edge(path[-1], target)
    assert path[0] != (source.cx, source.cy)
    assert path[-1] != (target.cx, target.cy)
    assert not path_hits_box_interior(path, other)
    assert not path_hits_box_interior(path, source)
    assert not path_hits_box_interior(path, target)


def test_org_bus_paths_are_orthogonal_and_attach_on_edges() -> None:
    lead = _box(5.0, 4.0, 2.6, 1.0)
    units = [_box(1.2 + index * 2.2, 1.4, 1.9, 1.0) for index in range(4)]
    paths = org_bus_paths(lead, units)
    assert paths
    for path in paths:
        assert is_orthogonal(path)
    drops = paths[2:]
    for unit, drop in zip(units, drops, strict=True):
        assert is_outside_near_edge(drop[-1], unit)


def test_end_node_paint_uses_accent_not_step_fill() -> None:
    style = resolve_diagram_style(None)
    step = paint_for_kind(style, "step")
    end = paint_for_kind(style, "end")
    start = paint_for_kind(style, "start")
    assert step.fill != end.fill
    assert start.fill != step.fill
    assert end.stroke == style.accent

