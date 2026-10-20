"""
职责: 将 PDF 上下标字符回拼到宿主文本行以避免公式下标乱序。
不做: 不做 OCR、不改变字符内容、不推断表格结构。
"""

from __future__ import annotations

from statistics import median
from typing import Any


def lift_page_script_chars(chars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """返回坐标修正后的 chars，使下标/上标参与宿主行重组。"""
    if not chars:
        return chars

    copied = [dict(ch) for ch in chars]
    lines = _cluster_lines(copied)
    if len(lines) <= 1:
        return copied

    line_infos = [_line_info(line) for line in lines]
    for line, info in zip(lines, line_infos):
        if info is None:
            continue
        line_top, line_bottom, body_size = info
        for ch in line:
            size = _float(ch.get("size"))
            if size <= 0 or size >= body_size * 0.82:
                continue
            # 小字号字符只要与宿主行带有垂直相交, 就抬升到该行。
            if _overlaps(_float(ch.get("top")), _float(ch.get("bottom")), line_top, line_bottom):
                _move_char_to_band(ch, line_top, line_bottom)

    _snap_adjacent_script_chars(copied)
    _reattach_orphan_script_lines(lines, line_infos)
    return sorted(copied, key=lambda ch: (_float(ch.get("doctop", ch.get("top"))), _float(ch.get("x0"))))


def text_from_chars(chars: list[dict[str, Any]], *, x_gap: float = 1.5, y_gap: float = 3.0) -> str:
    """轻量文本重组，供单元测试和局部 cell 文本修复使用。"""
    if not chars:
        return ""
    sorted_chars = sorted(chars, key=lambda ch: (_float(ch.get("top")), _float(ch.get("x0"))))
    lines: list[list[dict[str, Any]]] = []
    for ch in sorted_chars:
        center = (_float(ch.get("top")) + _float(ch.get("bottom"))) / 2
        if not lines:
            lines.append([ch])
            continue
        last = lines[-1]
        last_center = median([
            (_float(item.get("top")) + _float(item.get("bottom"))) / 2
            for item in last
        ])
        if abs(center - last_center) <= y_gap:
            last.append(ch)
        else:
            lines.append([ch])

    texts: list[str] = []
    for line in lines:
        parts: list[str] = []
        prev_x1: float | None = None
        for ch in sorted(line, key=lambda item: _float(item.get("x0"))):
            text = str(ch.get("text", ""))
            x0 = _float(ch.get("x0"))
            if prev_x1 is not None and x0 - prev_x1 > x_gap and _needs_space(parts[-1], text):
                parts.append(" ")
            parts.append(text)
            prev_x1 = _float(ch.get("x1"))
        texts.append("".join(parts).strip())
    return "\n".join(text for text in texts if text)


def chars_in_bbox(
    chars: list[dict[str, Any]],
    bbox: tuple[float, float, float, float],
    *,
    tolerance: float = 1.0,
) -> list[dict[str, Any]]:
    """筛选 bbox 内字符。bbox 为 pdfplumber 的 (x0, top, x1, bottom)。"""
    x0, top, x1, bottom = bbox
    return [
        ch for ch in chars
        if _float(ch.get("x0")) >= x0 - tolerance
        and _float(ch.get("x1")) <= x1 + tolerance
        and _float(ch.get("top")) >= top - tolerance
        and _float(ch.get("bottom")) <= bottom + tolerance
    ]


def _cluster_lines(chars: list[dict[str, Any]], *, tolerance: float = 4.0) -> list[list[dict[str, Any]]]:
    lines: list[list[dict[str, Any]]] = []
    for ch in sorted(chars, key=lambda item: ((_float(item.get("top")) + _float(item.get("bottom"))) / 2, _float(item.get("x0")))):
        center = (_float(ch.get("top")) + _float(ch.get("bottom"))) / 2
        if not lines:
            lines.append([ch])
            continue
        current = lines[-1]
        current_center = median([
            (_float(item.get("top")) + _float(item.get("bottom"))) / 2
            for item in current
        ])
        if abs(center - current_center) <= tolerance:
            current.append(ch)
        else:
            lines.append([ch])
    return lines


def _line_info(line: list[dict[str, Any]]) -> tuple[float, float, float] | None:
    sizes = [_float(ch.get("size")) for ch in line if _float(ch.get("size")) > 0]
    if not sizes:
        return None
    body_size = median(sizes)
    body_chars = [ch for ch in line if _float(ch.get("size")) >= body_size * 0.9]
    if not body_chars:
        body_chars = line
    return (
        min(_float(ch.get("top")) for ch in body_chars),
        max(_float(ch.get("bottom")) for ch in body_chars),
        body_size,
    )


def _reattach_orphan_script_lines(
    lines: list[list[dict[str, Any]]],
    line_infos: list[tuple[float, float, float] | None],
) -> None:
    for idx, line in enumerate(lines):
        text = "".join(str(ch.get("text", "")) for ch in line).strip()
        if not text or len(text) > 4 or not all(ch.isalnum() for ch in text):
            continue
        current_info = line_infos[idx]
        if current_info is None:
            continue
        current_size = current_info[2]
        candidates: list[tuple[float, tuple[float, float, float]]] = []
        for other_idx, info in enumerate(line_infos):
            if other_idx == idx or info is None or info[2] <= current_size * 1.2:
                continue
            distance = min(
                abs(current_info[0] - info[1]),
                abs(current_info[1] - info[0]),
            )
            if distance <= max(8.0, info[2]):
                candidates.append((distance, info))
        if not candidates:
            continue
        _, target = min(candidates, key=lambda item: item[0])
        for ch in line:
            _move_char_to_band(ch, target[0], target[1])


def _snap_adjacent_script_chars(chars: list[dict[str, Any]]) -> None:
    body_chars = [ch for ch in chars if _float(ch.get("size")) >= 8.0]
    for ch in chars:
        size = _float(ch.get("size"))
        text = str(ch.get("text", ""))
        if size <= 0 or size >= 8.0 or not text.strip().isalnum():
            continue
        candidates: list[tuple[float, dict[str, Any]]] = []
        for body in body_chars:
            gap = abs(_float(ch.get("x0")) - _float(body.get("x1")))
            if gap > 2.0:
                continue
            if not _overlaps(
                _float(ch.get("top")),
                _float(ch.get("bottom")),
                _float(body.get("top")),
                _float(body.get("bottom")),
            ):
                continue
            candidates.append((gap, body))
        if not candidates:
            continue
        _, host = min(candidates, key=lambda item: item[0])
        _copy_vertical_band(ch, host)


def _move_char_to_band(ch: dict[str, Any], top: float, bottom: float) -> None:
    old_top = _float(ch.get("top"))
    old_bottom = _float(ch.get("bottom"))
    height = max(old_bottom - old_top, 1.0)
    target_bottom = bottom
    target_top = target_bottom - height
    ch["top"] = target_top
    ch["bottom"] = target_bottom
    if "doctop" in ch:
        ch["doctop"] = target_top


def _copy_vertical_band(ch: dict[str, Any], host: dict[str, Any]) -> None:
    ch["top"] = _float(host.get("top"))
    ch["bottom"] = _float(host.get("bottom"))
    if "doctop" in ch:
        ch["doctop"] = _float(host.get("doctop", host.get("top")))


def _overlaps(top_a: float, bottom_a: float, top_b: float, bottom_b: float) -> bool:
    return min(bottom_a, bottom_b) - max(top_a, top_b) > -2.0


def _needs_space(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return left[-1].isascii() and right[0].isascii() and left[-1].isalnum() and right[0].isalnum()


def _float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
