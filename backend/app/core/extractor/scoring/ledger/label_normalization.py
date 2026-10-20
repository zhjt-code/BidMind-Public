"""组标签归一（账本匹配键，探针实测口径固化）。

职责: 将原文组标签归一为账本匹配键——NFKC 全半角归一、去空白、迭代剥离
      尾部括注分值与组语义尾缀、小写化、领域同义键折叠；并提供总分/合计类
      标签的跳过判定。
不做: 不做枚举卷映射、不做语义推断、不裁决组归属。
"""
from __future__ import annotations

import re
import unicodedata

# 组语义尾缀（探针实测集: 裁决集 + 等价表述追加项; "表"覆盖"商务评审标准表"）。
_LABEL_SUFFIXES = (
    "评分标准", "评审标准", "评分", "评审", "标准", "部分", "卷", "标", "项", "分", "表",
)
_SKIP_TERMS = ("总分", "合计", "满分", "总计", "分值构成", "分值分配", "分值组成", "得分")
_WHITESPACE_RE = re.compile(r"\s+")
# 尾部括注分值 "(16分)": 原文冗余修饰而非标签实义。NFKC 已把全角
# 括号归一为半角、去空白已执行, 故只需匹配半角紧凑形态。
_PAREN_SCORE_TAIL_RE = re.compile(r"\(\d+(?:\.\d+)?分\)$")
# 尾部短编号标记: "磋商报价评审(F1)"与"磋商报价部分F1"实义同为
# "磋商报价", 编号标记是修饰。仅剥"字母+至多2位数字"编号形态(f1/a12)——
# 纯字母缩写"(ESG)"是实义不得剥(既有裁决), CJK+数字("采购包1")同为实义。
# 若两个原本不同的标签因此折叠同键, 命中 同域冲突拒用 fail-visible。
_PAREN_MARKER_TAIL_RE = re.compile(r"\([a-z]\d{1,2}\)$")
_BARE_MARKER_TAIL_RE = re.compile(r"[a-z]\d{1,2}$")
# 首部括注标注 "(明标)资信业绩评分标准": 评审形式标注是修饰而非标签实义。
# 只剥**首部完整括注**, 中缀/尾缀括注携带区分信息(如"详细评审(商务)"), 不得触碰。
_PAREN_HEAD_RE = re.compile(r"^\([^()]*\)")
# 中缀功能词 "评分/评审": 与尾缀剥离同性质的冗余修饰消解(如声明侧"其他评分因素"
# vs 项侧"其他因素评分标准", 两者实义同为"其他因素")。这不是同义联想——只删除
# 功能词本身, 不改写任何实义字; 若两个原本不同的标签因此折叠同键, 会命中
# 同域冲突拒用, fail-visible 而非静默错配。
_INFIX_FUNCTION_TERMS = ("评分", "评审")
# 评标领域稳定同义键折叠: 报价/价格指同一评审维度。仅折叠剥尾缀后的
# 精确键(不做复合词联想); 同文档若并存不同分值的两条声明, 折叠后同键不同值会
# 命中 冲突拒用, fail-visible 而非静默错配。
_SYNONYM_FOLD = {"报价": "价格"}


def _strip_decorations_to_fixpoint(text: str) -> str:
    """迭代剥离首部括注/尾部括注分值/组语义尾缀/残缺括号片段, 直至不动点。"""
    changed = True
    while changed:
        changed = False
        head = _PAREN_HEAD_RE.match(text)
        if head is not None and head.end() < len(text):
            text = text[head.end():]
            changed = True
            continue
        stripped = text.rstrip("( ").lstrip(") ")
        if stripped != text and stripped:
            text = stripped
            changed = True
            continue
        paren = _PAREN_SCORE_TAIL_RE.search(text)
        if paren is not None and paren.start() > 0:
            text = text[: paren.start()]
            changed = True
            continue
        marker = _PAREN_MARKER_TAIL_RE.search(text)
        if marker is not None and marker.start() > 0:
            text = text[: marker.start()]
            changed = True
            continue
        bare = _BARE_MARKER_TAIL_RE.search(text)
        if bare is not None and bare.start() >= 2:
            text = text[: bare.start()]
            changed = True
            continue
        for suffix in _LABEL_SUFFIXES:
            if len(text) > len(suffix) and text.endswith(suffix):
                text = text[: -len(suffix)]
                changed = True
                break
    return text


def normalize_group_label(label: str) -> str:
    """组标签归一: NFKC+去空白+剥修饰(括注/尾缀/中缀功能词)+括号折叠+小写+同义折叠。空返回空串。"""
    text = unicodedata.normalize("NFKC", str(label or ""))
    text = _WHITESPACE_RE.sub("", text).lower()
    # Markdown 表格竖线和常规标点是载体边界，不属于开放集合组名。
    # 锚点层已清理一次，此处作为跨来源归一闸门兜底。
    text = text.strip("|:：;；,，。")
    text = _strip_decorations_to_fixpoint(text)
    for term in _INFIX_FUNCTION_TERMS:
        without = text.replace(term, "")
        if without != text and len(without) >= 2:
            text = _strip_decorations_to_fixpoint(without)
    # 括号字符是载体修饰(封闭字符类), 折叠出键但保留括注内容——
    # "详细评审(技术)"与"详细评审技术"实义相同必须同键(3477 组轴归零实测)。
    # 若两个原本不同的标签因此撞键, 命中 同域冲突拒用 fail-visible。
    folded = text.replace("(", "").replace(")", "")
    if folded != text and folded:
        text = _strip_decorations_to_fixpoint(folded)
    return _SYNONYM_FOLD.get(text, text)


def is_total_like_label(label: str) -> bool:
    """标签是否为总分/合计类（不得作为组声明对标签）。"""
    compact = _WHITESPACE_RE.sub("", str(label or ""))
    return (not compact) or any(term in compact for term in _SKIP_TERMS)


__all__ = ["is_total_like_label", "normalize_group_label"]
