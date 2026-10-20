"""
职责: 对目录规格提取结果做原文对齐校验、编号捕获与覆盖率评估
不做: 不调用LLM、不做选段、不访问数据库
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.schemas.writing import BidDirectoryItem, BidDirectorySpec


# ─── 编号行识别正则（行首，优先匹配较长模式，避免前缀截断） ────────────────────
# 分支优先级说明：
#   连字符层级 1-1/2-1-1 > 点号层级 1.1 > 数字+顿号/点号 > 纯数字+空格
_NUMBERED_LINE_RE = re.compile(
    r"^("
    r"[一二三四五六七八九十百千]+[、]"      # 一、二、三、
    r"|[（(]\d+[）)]"                       # (1)  （1）
    r"|\d+(?:-\d+)+"                        # 1-1  2-1-1
    r"|\d+(?:\.\d+)+"                       # 1.1  1.1.1
    r"|\d+[、]"                             # 1、
    r"|\d+\."                               # 1.
    r"|\d+(?=[ \t])"                        # 1<空格>  10<空格>
    r")\s*(.*)",
    re.MULTILINE,
)

# 行尾装饰性点线（如目录页的 "............" "----"）及其后内容去除
_TRAILING_DECORATION_RE = re.compile(r"[.·…\-]{3,}.*$")
# 目录条目标题上限：超过则视为正文/表格噪声，不进入清单
_MAX_DIRECTORY_TITLE_CHARS = 80


def _normalize(text: str) -> str:
    """去除所有空白，并将全角括号归一化为半角，用于宽松标题匹配。"""
    t = re.sub(r"\s+", "", text)
    return t.replace("（", "(").replace("）", ")")


def _candidate_numbered_lines(source_text: str) -> list[str]:
    """生产拼接文本只取每个 chunk 块的首行，避免模板正文编号污染清单。"""

    if not re.search(r"\n\s*\n", source_text):
        return source_text.splitlines()
    candidates: list[str] = []
    for block in re.split(r"\n\s*\n", source_text):
        first_line = next((line.strip() for line in block.splitlines() if line.strip()), "")
        if first_line:
            candidates.append(first_line)
    return candidates


@dataclass(frozen=True)
class NumberedLine:
    """原文中识别到的一条编号行。"""

    display_no: str   # 行首编号，如 一、/ 1-1 / 2-1-1
    title: str        # 编号之后的标题文本（已 strip，已去装饰）
    raw_line: str     # 原始完整行（含编号前缀）


@dataclass(frozen=True)
class SpecCoverageReport:
    """目录规格与原文编号清单的覆盖率评估报告。"""

    status: str           # "complete" | "partial"
    inventory_count: int
    extracted_count: int
    missing_titles: list[str]       # inventory 中有但 spec 中无的标题
    unverified_titles: list[str]    # spec 中 source_verified 为 False 的标题
    warning: str                    # 中文简述，如疑似截断位置


def build_numbered_line_inventory(source_text: str) -> list[NumberedLine]:
    """从原文中提取有编号的目录行，归一化标题后去重（保留首次出现）。

    去重策略：目录页与正文模板可能对同一标题重复编排，
    按归一化标题去重后，inventory_count 仅反映不同条目数量。

    参数:
        source_text: 招标文件原始文本（多行）

    返回:
        识别到的 NumberedLine 列表，顺序与原文一致
    """
    seen_norm: set[str] = set()
    result: list[NumberedLine] = []

    for line in _candidate_numbered_lines(source_text):
        match = _NUMBERED_LINE_RE.match(line)
        if not match:
            continue
        display_no = match.group(1).strip()
        raw_title = _TRAILING_DECORATION_RE.sub("", match.group(2)).strip()
        if not raw_title or len(raw_title) > _MAX_DIRECTORY_TITLE_CHARS:
            continue
        if raw_title.startswith("表格"):
            continue

        norm = _normalize(raw_title)
        if norm in seen_norm:
            continue
        seen_norm.add(norm)

        result.append(
            NumberedLine(display_no=display_no, title=raw_title, raw_line=line.strip())
        )

    return result


def verify_spec_against_source(
    spec: BidDirectorySpec,
    source_text: str,
) -> BidDirectorySpec:
    """对 spec 中每个目录条目做原文对齐，填充 source_verified 和 display_no。

    对齐规则：
    - source_verified=True 当归一化 title 是归一化 source_text 的子串
    - display_no 从编号清单按归一化标题匹配，无法匹配时保持 None

    返回新的 BidDirectorySpec，不修改传入对象。

    参数:
        spec: 待校验的目录规格
        source_text: 招标文件原始文本

    返回:
        携带 source_verified / display_no 注解的新 BidDirectorySpec
    """
    inventory = build_numbered_line_inventory(source_text)
    norm_source = _normalize(source_text)

    # 归一化标题 → display_no（首次出现优先）
    title_to_no: dict[str, str] = {}
    for inv_item in inventory:
        key = _normalize(inv_item.title)
        if key not in title_to_no:
            title_to_no[key] = inv_item.display_no

    def _annotate(item: BidDirectoryItem) -> BidDirectoryItem:
        norm_title = _normalize(item.title)
        return item.model_copy(update={
            "source_verified": norm_title in norm_source,
            "display_no": title_to_no.get(norm_title),
            "children": [_annotate(c) for c in item.children],
        })

    new_volumes = [
        vol.model_copy(update={"items": [_annotate(it) for it in vol.items]})
        for vol in spec.volumes
    ]
    return spec.model_copy(update={"volumes": new_volumes})


def evaluate_spec_coverage(
    spec: BidDirectorySpec,
    inventory: list[NumberedLine],
) -> SpecCoverageReport:
    """计算目录规格与原文编号清单的覆盖率。

    各指标定义：
    - extracted_count: spec 树展平后所有 item 节点数（不含 volume 自身）
    - inventory_count: inventory 条数（已按归一化标题去重）
    - missing_titles: inventory 中归一化标题不在 spec 覆盖范围内的条目
      （卷名也纳入覆盖范围，避免结构层级标题被误判为缺失）
    - unverified_titles: spec 中 source_verified 明确为 False 的条目
    - status: partial 若 missing_titles 非空，否则 complete

    参数:
        spec: 目录规格（建议先经 verify_spec_against_source 注解）
        inventory: 原文编号清单

    返回:
        SpecCoverageReport 含中文 warning 描述
    """
    def _flatten(items: list[BidDirectoryItem]) -> list[BidDirectoryItem]:
        out: list[BidDirectoryItem] = []
        for item in items:
            out.append(item)
            out.extend(_flatten(item.children))
        return out

    flat_items: list[BidDirectoryItem] = []
    for vol in spec.volumes:
        flat_items.extend(_flatten(vol.items))

    extracted_count = len(flat_items)
    inventory_count = len(inventory)

    # 覆盖集合：item 标题 + 卷名（避免将结构层级标题误报为缺失）
    covered_norm: set[str] = {_normalize(it.title) for it in flat_items}
    for vol in spec.volumes:
        covered_norm.add(_normalize(vol.volume_title))

    missing_titles = []
    for inv_item in inventory:
        inventory_title = _normalize(inv_item.title)
        if not any(inventory_title.startswith(title) for title in covered_norm):
            missing_titles.append(inv_item.title)

    unverified_titles = [
        it.title for it in flat_items if it.source_verified is False
    ]

    status = "partial" if missing_titles else "complete"

    if inventory_count == 0 and extracted_count == 0:
        warning = "原文中未检测到编号行，目录清单为空"
    elif missing_titles:
        last = missing_titles[-1]
        snippet = "、".join(missing_titles[:3])
        suffix = "等" if len(missing_titles) > 3 else ""
        warning = (
            f"原文编号清单共 {inventory_count} 条，"
            f"目录规格仅提取 {extracted_count} 项，"
            f"疑似截断于「{last}」之前，未提取：{snippet}{suffix}"
        )
    else:
        warning = ""

    return SpecCoverageReport(
        status=status,
        inventory_count=inventory_count,
        extracted_count=extracted_count,
        missing_titles=missing_titles,
        unverified_titles=unverified_titles,
        warning=warning,
    )


def render_structure_line_view(source_text: str) -> str:
    """把原文编号行渲染为浓缩结构视图，供超预算时替代钝截断。"""

    return "\n".join(
        f"{item.display_no} {item.title}"
        for item in build_numbered_line_inventory(source_text)
    )
