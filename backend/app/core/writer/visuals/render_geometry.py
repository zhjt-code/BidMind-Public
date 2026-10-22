"""图元几何：贴边锚点与正交折线。

职责: 计算节点包围盒、边锚点、正交路由，并判断折线是否穿入框内。
不做: 不渲染 PNG、不编译 IR、不访问数据库。
"""

from __future__ import annotations

from dataclasses import dataclass

EPS = 1e-6
ATTACH_GAP = 0.12
_DECISION_MARKS = ("?", "？", "是否", "有无")
_SAME_ROW_DY_RATIO = 0.35


@dataclass(frozen=True)
class Box:
    """轴对齐包围盒，中心点 + 宽高。"""

    cx: float
    cy: float
    width: float
    height: float

    @property
    def left(self) -> float:
        return self.cx - self.width / 2

    @property
    def right(self) -> float:
        return self.cx + self.width / 2

    @property
    def bottom(self) -> float:
        return self.cy - self.height / 2

    @property
    def top(self) -> float:
        return self.cy + self.height / 2


def infer_flowchart_kind(index: int, total: int, label: str) -> str:
    """按位置与标签推断 start/step/decision/end。"""

    text = label or ""
    if any(mark in text for mark in _DECISION_MARKS):
        return "decision"
    if index == 0:
        return "start"
    if total > 0 and index == total - 1:
        return "end"
    return "step"


def edge_point(box: Box, side: str) -> tuple[float, float]:
    """返回指定边中点（贴框，不取盒心）。"""

    if side == "left":
        return (box.left, box.cy)
    if side == "right":
        return (box.right, box.cy)
    if side == "top":
        return (box.cx, box.top)
    if side == "bottom":
        return (box.cx, box.bottom)
    raise ValueError(f"unknown side: {side}")


def is_on_perimeter(point: tuple[float, float], box: Box, tol: float = 1e-6) -> bool:
    """点是否落在包围盒边框上。"""

    x_pos, y_pos = point
    on_vertical = (
        abs(x_pos - box.left) <= tol or abs(x_pos - box.right) <= tol
    ) and (box.bottom - tol <= y_pos <= box.top + tol)
    on_horizontal = (
        abs(y_pos - box.bottom) <= tol or abs(y_pos - box.top) <= tol
    ) and (box.left - tol <= x_pos <= box.right + tol)
    return on_vertical or on_horizontal


def nudge_outward(
    point: tuple[float, float],
    box: Box,
    gap: float = ATTACH_GAP,
) -> tuple[float, float]:
    """把贴边锚点沿法线外移，给箭头尖留空隙。"""

    x_pos, y_pos = point
    dist_left = abs(x_pos - box.left)
    dist_right = abs(x_pos - box.right)
    dist_bottom = abs(y_pos - box.bottom)
    dist_top = abs(y_pos - box.top)
    side = min(
        (dist_left, "left"),
        (dist_right, "right"),
        (dist_bottom, "bottom"),
        (dist_top, "top"),
        key=lambda item: item[0],
    )[1]
    if side == "left":
        return (box.left - gap, y_pos)
    if side == "right":
        return (box.right + gap, y_pos)
    if side == "bottom":
        return (x_pos, box.bottom - gap)
    return (x_pos, box.top + gap)


def is_outside_near_edge(
    point: tuple[float, float],
    box: Box,
    gap: float = ATTACH_GAP,
    tol: float = 0.02,
) -> bool:
    """锚点在框外且贴近对应边，不落在文字区。"""

    x_pos, y_pos = point
    interior = (
        box.left + EPS < x_pos < box.right - EPS
        and box.bottom + EPS < y_pos < box.top - EPS
    )
    if interior:
        return False
    dx_out = max(box.left - x_pos, 0.0, x_pos - box.right)
    dy_out = max(box.bottom - y_pos, 0.0, y_pos - box.top)
    dist = (dx_out ** 2 + dy_out ** 2) ** 0.5
    return dist <= gap + tol


def is_orthogonal(path: list[tuple[float, float]]) -> bool:
    """折线每一段是否轴对齐。"""

    if len(path) < 2:
        return False
    for (x1, y1), (x2, y2) in zip(path[:-1], path[1:], strict=True):
        if abs(x1 - x2) > EPS and abs(y1 - y2) > EPS:
            return False
    return True


def _ranges_overlap(left: float, right: float, other_left: float, other_right: float) -> bool:
    return left < other_right and right > other_left


def _segment_hits_interior(
    start: tuple[float, float],
    end: tuple[float, float],
    box: Box,
) -> bool:
    inner_left = box.left + EPS
    inner_right = box.right - EPS
    inner_bottom = box.bottom + EPS
    inner_top = box.top - EPS
    if inner_left >= inner_right or inner_bottom >= inner_top:
        return False
    x1, y1 = start
    x2, y2 = end
    if abs(y1 - y2) <= EPS:
        if not (inner_bottom < y1 < inner_top):
            return False
        x_lo, x_hi = sorted((x1, x2))
        return _ranges_overlap(x_lo, x_hi, inner_left, inner_right)
    if abs(x1 - x2) <= EPS:
        if not (inner_left < x1 < inner_right):
            return False
        y_lo, y_hi = sorted((y1, y2))
        return _ranges_overlap(y_lo, y_hi, inner_bottom, inner_top)
    return True


def path_hits_box_interior(
    path: list[tuple[float, float]],
    box: Box,
) -> bool:
    """正交折线是否进入框的内部（贴边不算）。"""

    if len(path) < 2:
        return False
    return any(
        _segment_hits_interior(start, end, box)
        for start, end in zip(path[:-1], path[1:], strict=True)
    )


def _finish_route(
    path: list[tuple[float, float]],
    source: Box,
    target: Box,
) -> list[tuple[float, float]]:
    finished = list(path)
    finished[0] = nudge_outward(finished[0], source)
    finished[-1] = nudge_outward(finished[-1], target)
    return finished


def route_between_boxes(source: Box, target: Box) -> list[tuple[float, float]]:
    """在两框之间走贴边正交折线：同行水平、同列垂直、换行/跨道走槽。"""

    dx = target.cx - source.cx
    dy = target.cy - source.cy
    same_row = abs(dy) <= _SAME_ROW_DY_RATIO * max(source.height, target.height)
    same_column = abs(dx) <= EPS
    if same_row:
        if dx >= 0:
            path = [edge_point(source, "right"), edge_point(target, "left")]
        else:
            path = [edge_point(source, "left"), edge_point(target, "right")]
        return _finish_route(path, source, target)

    if same_column or dx < -source.width * 0.5:
        if source.cy > target.cy:
            alley_y = (source.bottom + target.top) / 2
            start = edge_point(source, "bottom")
            end = edge_point(target, "top")
        else:
            alley_y = (target.bottom + source.top) / 2
            start = edge_point(source, "top")
            end = edge_point(target, "bottom")
        return _finish_route(
            [start, (source.cx, alley_y), (target.cx, alley_y), end],
            source,
            target,
        )

    if dx >= 0:
        start = edge_point(source, "right")
        end = edge_point(target, "left")
        alley_x = (source.right + target.left) / 2
        if target.left <= source.right + EPS:
            alley_x = max(source.right, target.right) + 0.2
    else:
        start = edge_point(source, "left")
        end = edge_point(target, "right")
        alley_x = (source.left + target.right) / 2
        if source.left <= target.right + EPS:
            alley_x = min(source.left, target.left) - 0.2
    return _finish_route(
        [start, (alley_x, source.cy), (alley_x, target.cy), end],
        source,
        target,
    )


def org_bus_paths(lead: Box, units: list[Box]) -> list[list[tuple[float, float]]]:
    """组织图：主管下沿 → 水平总线 → 各部门上沿，全部正交。"""

    if not units:
        return []
    bus_y = (lead.bottom + max(unit.top for unit in units)) / 2
    xs = [lead.cx, *[unit.cx for unit in units]]
    stem = [nudge_outward((lead.cx, lead.bottom), lead), (lead.cx, bus_y)]
    paths = [
        stem,
        [(min(xs), bus_y), (max(xs), bus_y)],
    ]
    for unit in units:
        paths.append([(unit.cx, bus_y), nudge_outward((unit.cx, unit.top), unit)])
    return paths


__all__ = [
    "ATTACH_GAP",
    "Box",
    "edge_point",
    "infer_flowchart_kind",
    "is_on_perimeter",
    "is_orthogonal",
    "is_outside_near_edge",
    "nudge_outward",
    "org_bus_paths",
    "path_hits_box_interior",
    "route_between_boxes",
]
