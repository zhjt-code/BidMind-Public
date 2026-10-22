"""转置最终分值轴解析（Task 4A v2）。

职责: 识别并解析评分办法中"卷权重×最终总分"汇总表的转置形态——
 标签行以各卷"X权重"作列标题，权重行列出百分比，
 公式行给出"综合得分=N"（N 可为 80/100/120 等任意正值）。
 产出最终分值制卷定义：volume_max = weight × expected_total。
不做: 不解析普通折算卷（framework_table_parser 职责）、
 不解析"加权得分"汇总行旧格式（parse_weight_summary_defs 旧路径）、
 不调用 LLM/DB、不拆分/合并行。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.extractor.scoring.volume_parser import VolumeDefinition

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# Gate 1A: 强标题信号（两词须同时出现）
_STRONG_TITLE_A = "权重表"
_STRONG_TITLE_B = "综合得分汇总"

# 综合公式中的总分值：匹配"=数值"或"＝数值"（中文等号）。
# \b 在 CJK 文本中可正常充当右边界：数字是"词字符"，汉字不是，
# 故"=100分"中 '0' 与 '分' 之间会触发 \b，正确捕获 100 而非截断。
# 仅含"=0.40"等系数行（无 >=2 个卷关键词）由 _find_formula_row_after
# 的 len(vols) >= 2 门禁拒绝，正则层无需额外过滤。
_FORMULA_TOTAL_RE = re.compile(r"[=＝]\s*(\d+(?:\.\d+)?)\b")

# 百分比单元格（整格为纯百分比，如 "20%"、"40.5%"）
_PCT_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*[%％]$")

# 等式信号（用于标签行排除：含 "=" 或 "＝" 的行不得用作标签行）
_EQ_RE = re.compile(r"[=＝]")

# 卷关键词→枚举键（顺序不影响正确性，但保持一致性）
_ZH_VOLUME_MAP: tuple[tuple[str, str], ...] = (
    ("技术", "technical"),
    ("价格", "price"),
    ("商务", "commercial"),
    ("服务", "service"),
    ("资信", "qualification"),
)

# 枚举键→原文标签（供 VolumeDefinition.label）
_VOLUME_LABELS: dict[str, str] = {
    "technical": "技术标",
    "price": "价格标",
    "commercial": "商务标",
    "service": "服务标",
    "qualification": "资信标",
}


# ---------------------------------------------------------------------------
# 公开数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FinalScoreAxis:
    """转置最终分值轴：各卷权重 + 期望最终分值。

    expected_by_volume[vol] = weights[vol] × expected_total
    （如 20/40/40，不代表各卷100分再折算）。
    """

    expected_total: float
    expected_by_volume: dict[str, float]
    weights: dict[str, float]
    source_page: int | None = None


# ---------------------------------------------------------------------------
# 主解析函数
# ---------------------------------------------------------------------------


def parse_transposed_final_score_axis(
    grid: list[list[str]], *, source_page: int | None = None
) -> FinalScoreAxis | None:
    """解析转置最终分值轴汇总表。

    门禁（任一不满足返回 None）：
    1. Gate I5  全表须有强结构信号（标题含"权重表+综合得分汇总"，或 >=2 个"X权重"列头）
    2. Gate I1  唯一标签行（含>=2不同"X权重"列，无百分比，无等式，多候选 fail-closed）
    3. Gate     至少2个不同卷标签
    4. Gate I3  公式行在标签行之后（只在标签行之后搜索公式）
    5. Gate C2  expected_total > 0（不硬编码≈100）
    6. Gate I2  标签行与公式行之间恰有一个完整有效权重行（多个→拒绝）
    7. Gate     标签列与百分比列数量一致（无孤儿）
    8. Gate     权重之和 = 1.0 ± 0.01
    9. Gate     公式覆盖全部声明卷
    """
    if not grid:
        return None

    flat = "".join(cell for row in grid for cell in row).replace(" ", "")
    if not flat:
        return None

    # Gate 1 (I5): 强结构信号
    if not _has_structural_signal(flat, grid):
        return None

    # Gate 2 (I1): 找唯一标签行；多候选 fail-closed
    label_candidates = _find_label_row_candidates(grid)
    if len(label_candidates) != 1:
        return None  # 0 个或 >1 个均拒绝

    label_row_idx, label_col_map = label_candidates[0]

    # Gate 3: 至少2个不同卷
    if len(label_col_map) < 2:
        return None

    # Gate 4 (I3): 在标签行之后寻找公式行（强制 formula_row > label_row）
    formula_result = _find_formula_row_after(grid, label_row_idx)
    if formula_result is None:
        return None
    formula_row_idx, expected_total, formula_vols = formula_result

    # Gate 5 (C2): expected_total > 0（支持任意正值，不限于≈100）
    if expected_total <= 0:
        return None

    # Gate 6 (I2+I3): 恰有一个完整有效权重行在 (label, formula) 区间内
    weight_rows = _find_weight_rows_between(
        grid, label_row_idx, formula_row_idx, label_col_map
    )
    if len(weight_rows) != 1:
        return None  # 0个 or >1个权重行均拒绝

    weights_by_volume = weight_rows[0]

    # Gate 7: 标签列与百分比列数量一致（_find_weight_rows_between 已保证，防御性检查）
    if len(weights_by_volume) != len(label_col_map):
        return None

    # Gate 8: 权重之和 = 1.0 ± 0.01
    if abs(sum(weights_by_volume.values()) - 1.0) > 0.01:
        return None

    # Gate 9: 公式必须覆盖全部声明卷
    declared_vols = set(label_col_map.keys())
    if not declared_vols <= formula_vols:
        return None

    expected_by_volume = {
        vol: round(w * expected_total, 4)
        for vol, w in weights_by_volume.items()
    }

    return FinalScoreAxis(
        expected_total=expected_total,
        expected_by_volume=expected_by_volume,
        weights=weights_by_volume,
        source_page=source_page,
    )


# ---------------------------------------------------------------------------
# 转换函数
# ---------------------------------------------------------------------------


def final_score_axis_to_volume_defs(
    axis: FinalScoreAxis,
) -> dict[str, VolumeDefinition]:
    """将转置最终分值轴转换为卷定义字典（最终分值制）。

    volume_max = weight × expected_total（如 20/40/40），
    weight = axis.weights[vol]（直接权重，不从卷内100折算）。
    """
    return {
        vol: VolumeDefinition(
            volume=vol,
            weight=w,
            volume_max=round(w * axis.expected_total, 4),
            label=_VOLUME_LABELS.get(vol),
        )
        for vol, w in axis.weights.items()
    }


# ---------------------------------------------------------------------------
# 内部工具函数
# ---------------------------------------------------------------------------


def _has_structural_signal(flat: str, grid: list[list[str]]) -> bool:
    """Gate 1 (I5): 表须有强结构信号，不以'权重名称'单词单独开闸。

    路径 A（标题级）: 全表扁平文本同时含'权重表'+'综合得分汇总'。
    路径 B（结构级）: 全表至少 2 个不同卷的"X权重"列头单元格。
    """
    # 路径 A
    if _STRONG_TITLE_A in flat and _STRONG_TITLE_B in flat:
        return True
    # 路径 B
    found_vols: set[str] = set()
    for row in grid:
        for cell in row:
            vol = _cell_to_volume_weight_header(cell)
            if vol is not None:
                found_vols.add(vol)
                if len(found_vols) >= 2:
                    return True
    return False


def _find_label_row_candidates(
    grid: list[list[str]],
) -> list[tuple[int, dict[str, int]]]:
    """找所有符合条件的标签行候选（I1）。

    候选条件：
    - 每个识别单元格须含唯一卷关键词 + '权重'（"X权重"格）
    - 行中不含纯百分比单元格（排除权重行误判为标签行）
    - 行中不含等式字符（I1: 公式行不得劫持标签行）
    - >=2 个不同卷列（且无重复卷）

    返回 [(row_idx, {vol: col_idx}), ...]。
    """
    candidates: list[tuple[int, dict[str, int]]] = []
    for row_idx, row in enumerate(grid):
        row_text = "".join(row).replace(" ", "")
        # I1 排除: 含等式的行（公式行防劫持）
        if _EQ_RE.search(row_text):
            continue
        # I1 排除: 含纯百分比单元格的行（权重行防混入）
        if any(_parse_pct(cell) is not None for cell in row):
            continue
        # 扫描"X权重"列
        col_map: dict[str, int] = {}
        duplicate = False
        for col_idx, cell in enumerate(row):
            vol = _cell_to_volume_weight_header(cell)
            if vol is None:
                continue
            if vol in col_map:
                duplicate = True
                break
            col_map[vol] = col_idx
        if duplicate or len(col_map) < 2:
            continue
        candidates.append((row_idx, col_map))
    return candidates


def _find_formula_row_after(
    grid: list[list[str]],
    label_row_idx: int,
) -> tuple[int, float, set[str]] | None:
    """在标签行之后寻找综合公式行（I3: formula_row > label_row）。

    公式行条件：含"=数值"且数值 >0，同行含 >=2 个不同卷关键词。
    返回 (row_idx, expected_total, {volume_keys_in_formula})；未找到返回 None。
    """
    for row_idx in range(label_row_idx + 1, len(grid)):
        row_text = "".join(grid[row_idx]).replace(" ", "")
        m = _FORMULA_TOTAL_RE.search(row_text)
        if not m:
            continue
        try:
            total = float(m.group(1))
        except ValueError:
            continue
        if total <= 0:
            continue
        vols = {vol for kw, vol in _ZH_VOLUME_MAP if kw in row_text}
        if len(vols) >= 2:
            return row_idx, total, vols
    return None


def _find_weight_rows_between(
    grid: list[list[str]],
    label_row_idx: int,
    formula_row_idx: int,
    label_col_map: dict[str, int],
) -> list[dict[str, float]]:
    """收集 (label, formula) 区间内所有完整有效权重行（I2+I3）。

    完整有效权重行条件：
    - 标签列对应的列均有合法百分比（无孤儿标签）
    - 标签列以外不含额外百分比（无孤儿百分比列）

    返回 [{vol: weight}, ...]；I2 要求调用方检查 len==1。
    """
    label_col_set = set(label_col_map.values())
    results: list[dict[str, float]] = []
    for row_idx in range(label_row_idx + 1, formula_row_idx):
        row = grid[row_idx]
        pct_cols: dict[int, float] = {}
        for col_idx, cell in enumerate(row):
            w = _parse_pct(cell)
            if w is not None:
                pct_cols[col_idx] = w
        if len(pct_cols) < 2:
            continue
        # 孤儿百分比列 → 拒绝此行
        if not pct_cols.keys() <= label_col_set:
            continue
        # 孤儿标签列 → 拒绝此行
        if not label_col_set <= pct_cols.keys():
            continue
        results.append(
            {vol: pct_cols[col_idx] for vol, col_idx in label_col_map.items()}
        )
    return results


def _cell_to_volume_weight_header(cell: str) -> str | None:
    """识别"X权重"列标题单元格，返回唯一卷枚举键。

    要求：单元格含'权重'关键字 + 唯一卷关键词。
    多个卷关键词（M1 混合格）→ None；无'权重' → None。
    """
    c = str(cell or "").replace(" ", "")
    if "权重" not in c:
        return None
    matches = [vol for kw, vol in _ZH_VOLUME_MAP if kw in c]
    if len(matches) != 1:
        return None  # 0个（无卷词）or ≥2个（M1 混合格）均拒绝
    return matches[0]


def _parse_pct(cell: str) -> float | None:
    """解析纯百分比单元格（如 '20%'）→ 0.20；非纯百分比 → None。"""
    m = _PCT_RE.match(str(cell or "").strip())
    if not m:
        return None
    try:
        value = round(float(m.group(1)) / 100.0, 4)
    except ValueError:
        return None
    return value if 0 < value <= 1 else None


__all__ = [
    "FinalScoreAxis",
    "final_score_axis_to_volume_defs",
    "parse_transposed_final_score_axis",
]
