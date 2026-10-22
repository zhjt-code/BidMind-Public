"""评分卷原文标签提取工具。

职责: 从评分表短标题/分组合并单元格/表级标题句中提取卷名原文与卷别归属。
不做: 不推断评分项、不解析分值、不生成卷定义; 不在无分组上下文时按项名
 关键词造组标签。
"""

from __future__ import annotations

import re

# 表级卷标题句: "2.2 详细评审(商务) 满分:20.00 分" / "2.4 报价评审 满分:60.00 分"
# / "附表3 商务评审标准表(综合评分法)"(样本27形态)。
# 要求"详细评审"/"满分N分"/"评审标准表"强信号共现, 防止把正文中泛卷别词误当表级标题。
# Task4B: 新增"商务标/技术标/价格标评审标准(表)"与"价格标评审方法"结构。
_TABLE_VOLUME_TITLE_RE = re.compile(
    r"详细评审\s*[（(]\s*(商务|技术|服务|报价|价格|资信)\s*[）)]"
    r"|(商务|技术|服务|报价|价格|资信)(?:部分)?评审[^\n]{0,10}?满分\s*[:：]?\s*\d+(?:\.\d+)?\s*分"
    r"|(商务|技术|服务|报价|价格|资信)评审标准表"
    r"|(商务标|技术标|价格标)评审(?:标准(?:表)?|方法)"
)
# "服务"卷从严信号: 泛"服务"词(售后服务方案等评分项名)禁止判卷, 仅组/标题语境生效。
_SERVICE_VOLUME_STRICT_RE = re.compile(r"服务(?:部分|评审|标|卷)")


def detect_table_volume_title(page_text: str) -> str | None:
    """从表格起始页文本探测表级卷标题句(供 section_path 附加卷别证据)。

    仅当页内全部命中指向同一卷别时返回该标题原文; 多卷标题共存视为歧义返回 None,
    防止把后续表的标题错挂到当前表。
    """
    compact = re.sub(r"\s+", "", page_text or "")
    if not compact:
        return None
    titles: list[str] = []
    volumes: set[str] = set()
    for match in _TABLE_VOLUME_TITLE_RE.finditer(compact):
        keyword = match.group(1) or match.group(2) or match.group(3) or match.group(4) or ""
        if not keyword:
            continue
        titles.append(match.group(0))
        volumes.add(keyword)
    if len(volumes) != 1:
        return None
    return titles[0]


_SCORE_STANDARD_MARKERS: tuple[tuple[str, str, str], ...] = (
    ("经济标评分标准", "price", "经济标评分标准"),
    ("技术标评分标准", "technical", "技术标评分标准"),
    ("资信业绩评分标准", "qualification", "资信业绩评分标准"),
    ("资信标评分标准", "qualification", "资信标评分标准"),
)


def volume_from_score_standard_marker(text: str) -> tuple[str, str] | None:
    """从项名/规则原文中的显式「X标评分标准」取 (volume键, 原文标签)；无标记返回 None。"""
    compact = str(text or "").replace(" ", "")
    if not compact:
        return None
    for phrase, volume, label in _SCORE_STANDARD_MARKERS:
        if phrase in compact:
            return volume, label
    return None


_EXPLICIT_BID_LABELS: dict[str, tuple[str, str]] = {
    "经济标": ("price", "经济标"),
    "技术标": ("technical", "技术标"),
    "商务标": ("commercial", "商务标"),
    "综合标": ("composite", "综合标"),
    "资信标": ("qualification", "资信标"),
}


def volume_from_explicit_bid_label(text: str) -> tuple[str, str] | None:
    """短标签「经济标/技术标/资信标」精确映射到卷键; 非精确短标签返回 None。"""
    compact = str(text or "").replace(" ", "")
    compact = re.sub(r"[（(]\s*\d+(?:\.\d+)?\s*分\s*[）)]$", "", compact)
    return _EXPLICIT_BID_LABELS.get(compact)


def infer_initial_table_volume(section_path: str) -> str:
    """按 section_path(含表级标题句)推断明细表初始卷别; 默认 technical。

    优先级: 价格/报价 > 商务/综合 > 技术 > 服务(从严) > 资信 > 默认 technical。
    技术必须先于服务判定, 防"技术服务部分"被泛服务信号错抢(样本3锁定形态)。
    """
    sp = re.sub(r"\s+", "", section_path or "")
    if "价格" in sp or "报价" in sp:
        return "price"
    if "商务" in sp or "综合" in sp:
        return "commercial"
    if "技术" in sp:
        return "technical"
    if _SERVICE_VOLUME_STRICT_RE.search(sp):
        return "service"
    if "资信" in sp:
        return "qualification"
    return "technical"


def service_volume_signal(text: str) -> bool:
    """文本是否含从严"服务"卷信号(服务部分/服务评审/服务标/服务卷)。"""
    return bool(_SERVICE_VOLUME_STRICT_RE.search(str(text or "").replace(" ", "")))


def volume_label_from_summary_row(text: str) -> str | None:
    """从评分汇总行文本中提取简短卷名标签。"""
    if "报价" in text:
        return "报价"
    if "价格" in text:
        return "价格"
    if "综合" in text:
        return "综合"
    if "商务" in text:
        return "商务"
    if "技术" in text:
        return "技术"
    return None


def infer_volume_label_from_row(row: list[str]) -> str | None:
    """从短卷标题行提取原文卷名标签。"""
    candidates = [_cell(row, i) for i in range(min(len(row), 4))]
    for cell in candidates:
        label = clean_volume_label(cell)
        if label and _volume_label_has_signal(label):
            return label
    return None


def infer_group_volume_label(row: list[str], content_i: int | None) -> str | None:
    """从内容列左侧的卷分组合并单元格提取原文卷名标签。"""
    limit = content_i if content_i is not None and content_i > 0 else 0
    for idx in range(min(limit, len(row))):
        label = clean_volume_label(_cell(row, idx))
        if label and _volume_label_has_signal(label):
            return label
    return None


def infer_scoring_group_label_from_row(row: list[str]) -> str | None:
    """从canonical分组标题行提取评分分组原文标签。

    这类标签是用户可见评分结构(如"主要人员/其他因素"), 不一定是内部volume枚举。
    """
    cells = [str(cell or "").strip() for cell in row]
    if not cells or not cells[0].replace(" ", ""):
        return None
    if any(cell.replace(" ", "") for cell in cells[1:]):
        return None
    non_empty = [cells[0].replace(" ", "")]
    if len(non_empty) != 1:
        return None
    label = non_empty[0].strip("：:;；,，。")
    if not label or len(label) > 30:
        return None
    if re.search(r"\d+(?:\.\d+)?\s*分", label):
        return None
    # 权重组头走 clean 剥「，权重N%」; 非卷名组标签 clean 为空则保留原文
    cleaned = clean_volume_label(label)
    return cleaned or label


def refine_scoring_group_label(label: str | None, item_name: str) -> str | None:
    """细化"其他因素"类泛化分组标签; 无分组上下文时禁止按项名造非价格组标签。

 边界依据(1707 vs 2471/2463 互为反例):
 - 价格类身份标签保留: 报价/评标价为公式计分单项, 组身份与项身份天然同一,
 且实测与声明侧键空间一致(1707 分值构成声明"评标价:10分");
 - 非价格关键词(业绩/信誉)仅在"其他因素"细分路径生效——label 为 None 时按
 项名造标签会脱离声明侧归一键空间, 并因非 None 而阻断 apply_volume_definitions
 的卷定义标签回填(2471"类似项目业绩"被造出"业绩", 致声明组"详细评审商务
 20分"假缺口9分; 2463"业绩"3分经同卷唯一标签继承放大为"业绩90分"错误标题)。
 遗留(同类排查已标记): 若文档声明粒度就是"其他因素N分"(如训练样本06),
 细分会反向制造失配, 该形态需要声明感知的对齐机制, 不在词面层解决。
 """
    if label and label not in {"其他因素", "其他评分因素"}:
        return label
    compact_name = str(item_name or "").replace(" ", "")
    if "评标价" in compact_name or "报价" in compact_name or "价格" in compact_name:
        return "评标价" if "评标价" in compact_name else "报价"
    if not label:
        return None
    if "信誉" in compact_name or "信用" in compact_name:
        return "信誉"
    if "业绩" in compact_name:
        return "业绩"
    return label


def clean_volume_label(text: str) -> str:
    """清理分组前缀和分值/权重后缀，保留用户可见卷名原文。"""
    compact = str(text or "").replace(" ", "")
    compact = re.sub(r"^[一二三四五六七八九十]+[、.．]", "", compact)
    match = re.search(r"(.+?(?:标|部分|卷))\s*[（(]?\s*\d+(?:\.\d+)?\s*分", compact)
    if match:
        return match.group(1)
    # 「技术部分，权重60%」→「技术部分」, 避免权重百分比污染组标签
    weight_match = re.match(
        r"^(.+?(?:标|部分|卷))[，,]?权重\d+(?:\.\d+)?%$", compact
    )
    if weight_match and _volume_label_has_signal(weight_match.group(1)):
        return weight_match.group(1)
    if len(compact) <= 30 and _volume_label_has_signal(compact):
        return compact.strip("：:;；,，。")
    return ""


def _volume_label_has_signal(label: str) -> bool:
    if any(k in label for k in ("报价", "价格", "商务", "综合", "技术", "资信", "信用", "信誉")):
        return True
    return service_volume_signal(label)


def _cell(row: list[str], idx: int) -> str:
    return str(row[idx] or "") if 0 <= idx < len(row) else ""


__all__ = [
    "clean_volume_label",
    "infer_group_volume_label",
    "infer_scoring_group_label_from_row",
    "infer_volume_label_from_row",
    "refine_scoring_group_label",
    "volume_from_explicit_bid_label",
    "volume_from_score_standard_marker",
    "volume_label_from_summary_row",
]
