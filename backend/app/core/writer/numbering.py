"""章节编号计算工具

基于目录树的 depth / sort_order 动态计算中文混合编号,
不存储到数据库(用户可随时调整目录顺序)。

编号体系(中文混合):
  depth 0  ->  不编号(顶级/分卷标题)
  depth 1  ->  一、二、三、...
  depth 2  ->  （一）（二）（三）...
  depth 3  ->  1、2、3、...
  depth 4  ->  （1）（2）（3）...
  depth 5  ->  ①②③...
  depth 6+ ->  1.1、1.2、1.3... (降级方案)
"""

from __future__ import annotations

import re
from typing import Optional

_CN_DIGITS = [
    "一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
    "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
    "二十一", "二十二", "二十三", "二十四", "二十五", "二十六", "二十七", "二十八", "二十九", "三十",
]

_CIRCLED_DIGITS = [
    "①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩",
    "⑪", "⑫", "⑬", "⑭", "⑮", "⑯", "⑰", "⑱", "⑲", "⑳",
]

_VISIBLE_NUMBERING_PATTERNS = [
    (1, re.compile(r"^[一二三四五六七八九十]+、\s*")),
    (2, re.compile(r"^（[一二三四五六七八九十]+）\s*|^\([一二三四五六七八九十]+\)\s*")),
    (6, re.compile(r"^\d+\.\d+[、.．]?\s*")),
    (3, re.compile(r"^\d+[、.．]\s*")),
    (4, re.compile(r"^（\d+）\s*|^\(\d+\)\s*|^\d+\)\s*")),
    (5, re.compile(r"^[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]\s*")),
]

_NUMBERING_DISPLAY_TEXT = {
    1: "一、二、三、...",
    2: "（一）（二）（三）...",
    3: "1、2、3、...",
    4: "（1）（2）（3）...",
    5: "①②③...",
}


_NUMBER_PREFIX_PATTERNS = [
    r"^[一二三四五六七八九十]+、\s*",
    r"^（[一二三四五六七八九十]+）\s*",
    r"^\([一二三四五六七八九十]+\)\s*",
    r"^第(?:[一二三四五六七八九十]+|\d+)(?:章|节|部分|篇|卷|编|条)\s*",
    r"^\d+(\.\d+)*[.、．]\s*",
    r"^\d+\u3000+",
    r"^\d+[\t ]+",
    r"^（\d+）\s*",
    r"^\(\d+\)\s*",
    r"^\d+\)\s*",
    r"^[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]\s*",
]


def _strip_number_prefixes(text: str, *, fallback_original: bool) -> str:
    """循环移除文本前缀编号，直到结果稳定。"""
    original = (text or "").strip()
    result = original

    while result:
        previous = result
        for pattern in _NUMBER_PREFIX_PATTERNS:
            result = re.sub(pattern, "", result).strip()
        if result == previous:
            break

    if not result and fallback_original:
        return original
    return result


def _cn(index: int) -> str:
    """从0开始的索引转中文数字(一~三十),超出范围降级为阿拉伯数字"""
    if 0 <= index < len(_CN_DIGITS):
        return _CN_DIGITS[index]
    return str(index + 1)


def _circled(index: int) -> str:
    """从0开始的索引转圆圈数字(①~⑳),超出范围降级为阿拉伯数字"""
    if 0 <= index < len(_CIRCLED_DIGITS):
        return _CIRCLED_DIGITS[index]
    return str(index + 1)


def _format_number(depth: int, sibling_index: int) -> str:
    """根据层级深度和同级序号生成编号字符串

    Args:
        depth: 节点深度(0=顶级)
        sibling_index: 在同级中的排序位置(从0开始)

    Returns:
        编号字符串,depth=0 返回空字符串
    """
    if depth == 0:
        return ""
    elif depth == 1:
        return f"{_cn(sibling_index)}、"
    elif depth == 2:
        return f"（{_cn(sibling_index)}）"
    elif depth == 3:
        return f"{sibling_index + 1}、"
    elif depth == 4:
        return f"（{sibling_index + 1}）"
    elif depth == 5:
        return _circled(sibling_index)
    # 超过五级时退化为 1.1、1.2 风格，避免继续复用错误的 1) 体系
    return f"1.{sibling_index + 1}、"


def describe_numbering_for_depth(depth: int) -> str:
    """返回指定层级使用的编号表现形式说明。"""
    return _NUMBERING_DISPLAY_TEXT.get(depth, "1.1、1.2、1.3...")


def format_number_for_depth(depth: int, sibling_index: int) -> str:
    """公开的编号格式化入口。"""
    return _format_number(depth, sibling_index)


def detect_visible_numbering_depth(text: str) -> Optional[int]:
    """识别文本中可见编号前缀对应的层级。

    该结果只表示“输入长得像第几层”，供结构解析作为弱提示使用，
    最终层级仍需结合目录基准深度与父子关系收敛。
    """
    stripped = (text or "").strip()
    for depth, pattern in _VISIBLE_NUMBERING_PATTERNS:
        if pattern.match(stripped):
            return depth
    return None


def get_min_child_depth(base_depth: int) -> int:
    """根据当前章节基准深度返回其正文子层级的最小允许深度。"""
    return max(base_depth + 1, 1)


def compute_chapter_numbers(
    nodes: list,
) -> dict[int, str]:
    """计算所有节点的章节编号

    输入完整目录树节点列表(需具有 id/parent_id/depth/sort_order 属性),
    返回 {node_id: "一、"} 编号映射。depth=0 的节点编号为空字符串。

    Args:
        nodes: WritingOutlineNode 列表(完整项目目录)

    Returns:
        {node_id: number_str} 映射
    """
    children_map: dict[Optional[int], list] = {}
    for n in nodes:
        children_map.setdefault(n.parent_id, []).append(n)

    for children in children_map.values():
        children.sort(key=lambda n: n.sort_order)

    number_map: dict[int, str] = {}

    def _recurse(parent_id: Optional[int]) -> None:
        siblings = children_map.get(parent_id, [])
        for idx, node in enumerate(siblings):
            number_map[node.id] = _format_number(node.depth, idx)
            _recurse(node.id)

    _recurse(None)
    return number_map


def get_sub_numbering_hint(depth: int) -> str:
    """根据当前章节的 depth,返回子标题编号规则提示文本

    供 SectionComposer 注入到 Prompt 中,告诉 LLM 该章节内部
    的子标题应该使用什么编号格式。

    Args:
        depth: 当前章节节点的深度

    Returns:
        子标题编号规则描述字符串
    """
    child_depth = get_min_child_depth(depth)
    return f"子标题使用 {describe_numbering_for_depth(child_depth)} 编号"


def strip_existing_number(title: str) -> str:
    """移除标题中已有的编号前缀

    LLM 生成的目录标题可能自带编号(如"一、公司简介"或"1.1 概述"),
    在我们统一编号时需要先剥离这些前缀,避免编号重复。

    Args:
        title: 原始章节标题

    Returns:
        去除编号前缀后的纯文本标题
    """
    return _strip_number_prefixes(title, fallback_original=True)


def strip_existing_number_strict(text: str) -> str:
    """正文严格清洗入口。

    与目录标题编辑不同，正文场景允许返回空串，以便上游识别纯编号块。
    """
    return _strip_number_prefixes(text, fallback_original=False)
