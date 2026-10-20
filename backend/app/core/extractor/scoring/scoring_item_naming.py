"""评分项名称净化。

职责: 剥离评分因素列的排版装饰(行序号前缀、尾部括号分值), 并在名称被长分组/
      汇总规则污染时从评分规则恢复真实短标签。
不做: 不改分值、不推断卷别、不剥离名称自带的编号后缀与去重消歧后缀。
"""

from __future__ import annotations

import re

_POLLUTION_TERMS = ("汇总规则", "专家数量", "算术平均值")

# 行序号前缀: "1、" / "10." / "一、" / "（1）"。要求数字后紧跟序号分隔符或成对括号,
# 故"2022年以来业绩"不受影响(数字后是"年")。
_ORDINAL_PREFIX_RE = re.compile(
    r"^(?:[（(]\s*(?:\d{1,2}|[一二三四五六七八九十]{1,3})\s*[）)]"
    r"|(?:\d{1,2}|[一二三四五六七八九十]{1,3})\s*[、.．)）])\s*"
)
# 尾部括号分值: "（3分）" / "(2分)" / 跨单元格截断的 "（3分"。必须含"分"字——
# 无"分"的"(2)"是去重消歧后缀, 剥离会造成身份重复。
_TRAILING_SCORE_SUFFIX_RE = re.compile(r"[（(]\s*\d+(?:\.\d+)?\s*分\s*[）)]?\s*$")
# 裸尾分值: "价格30分" / "技术性能58分" / "施工方案15.5分"（无括号包裹）。
# 尾锚 "$" 保护 "第3分册"（"分" 后还有 "册", 不在末尾匹配不上）；
# lookbehind 要求紧前为 CJK, 保护纯数字/ASCII前缀（如 "30分"、"abc30分"）。
# 单字剥离护栏见 strip_scoring_name_decoration: len<2 时回退 compact。
_BARE_TRAILING_SCORE_RE = re.compile(r"(?<=[\u4e00-\u9fff])\d+(?:\.\d+)?分$")

# 规则文本首部子项标签: "1.印刷服务方案：" / "应急预案:"。仅匹配首部,
# 序号可选; 冒号是子项名与说明正文的原文分隔证据。
_SUBITEM_LABEL_RE = re.compile(
    r"^(?:[（(]?\s*\d{1,2}\s*[）)]?\s*[、.．]?\s*)?([\u4e00-\u9fffA-Za-z0-9()]{2,20})\s*[：:]"
)
_SUBITEM_LABEL_WINDOW = 40
_SUBITEM_LABEL_SKIP_TERMS = ("注", "说明", "备注", "其中", "要求", "如下", "评分标准", "得分")
_GROUP_ONLY_RE = re.compile(r"^(?:技术|商务|资信|资格|价格|报价|综合|服务)(?:标|部分|卷)?(?:[（(]?\d+[）)]?)?$")
_TAIL_LABEL_RE = re.compile(r"([\u4e00-\u9fffA-Za-z0-9]{2,18})\s*$")
_LEADING_LABEL_RE = re.compile(r"^([\u4e00-\u9fffA-Za-z0-9、及与]{2,24})[：:；;]")
_BAD_TAIL_TERMS = (
    "不得分",
    "扣分",
    "扣完为止",
    "满分",
    "酌情扣分",
    "酌情得",
    "内容不全",
    "若此条缺项",
    "本项得0分",
    "使用直线",
    "重复计分",
)


def normalize_scoring_item_name(name: str, rule: str) -> str:
    """返回净化后的评分项名称。

    仅对明显污染名生效: 含汇总规则、过长、或只是卷/分组名。优先识别稳定业务
    主题，其次从规则尾部或首部短标签恢复，均不可信时保留原名称。
    """
    raw_compact = _compact(name)
    compact_name = _strip_layout_decoration(raw_compact)
    if not compact_name:
        return compact_name
    # "组名（N分）"形态剥离后即纯组名: 该行是自带分值的组级评分事实(如"报价部分 70分"),
    # 组名就是权威项名, 不得再走"纯卷名=污染"的规则恢复路径(否则被规则尾标签顶替)。
    if _TRAILING_SCORE_SUFFIX_RE.search(raw_compact) and _GROUP_ONLY_RE.match(compact_name):
        return compact_name
    if len(compact_name) > 30 and "评标价" in compact_name and "评标基准价" in compact_name:
        return "评标价"
    if len(compact_name) > 30 and "报价评分" in compact_name:
        return "报价评分"
    if len(compact_name) > 30 and "商务条款" in compact_name and "点对点应答" in compact_name:
        return "商务条款点对点应答"
    if len(compact_name) > 30 and "技术条款" in compact_name and "点对点应答" in compact_name:
        return "技术条款点对点应答"
    if len(compact_name) > 30 and "有效响应报价" in compact_name:
        return "有效响应报价"
    if not _looks_polluted(compact_name):
        return compact_name
    # 前置教训: 规则列尾部自带的短标签是评分表原文权威,
    # 必须优先于关键词式语义标签——语义标签对免责/交叉引用语境(如"不可与
    # 项目经理业绩重复计分")会误命中, 把"企业业绩"错命名成"项目经理及设计
    # 负责人业绩", 与真身行身份键碰撞后被精确去重吞行。
    tail = _tail_label(rule)
    if tail:
        return tail
    semantic = _semantic_rule_label(compact_name, rule)
    if semantic:
        return semantic
    leading = _leading_label(rule)
    return leading or compact_name


def recover_subitem_name_from_rule(rule: str) -> str:
    """从规则文本首部的 "N.标签：" / "标签：" 结构恢复子项原文名称。

    形态依据(样本27 p3 实测): 合并单元格"服务方案（承诺）"下的 5 个计分子行,
    真实子项名写在各自规则文本首部——"1.印刷服务方案：…"/"2.应急预案：…";
    这些名称是原文权威, 优于把父名 forward-fill 或产出占位符。
    仅扫描首部窗口, 且拒绝说明性引导词, 无可信标签返回空串。
    """
    text = _compact(rule)[:_SUBITEM_LABEL_WINDOW]
    match = _SUBITEM_LABEL_RE.match(text)
    if not match:
        return ""
    label = match.group(1).strip("：:；;，,。")
    if not (2 <= len(label) <= 20):
        return ""
    if any(term in label for term in _SUBITEM_LABEL_SKIP_TERMS):
        return ""
    if re.search(r"\d+(?:\.\d+)?分", label):
        return ""
    return label


def strip_scoring_name_decoration(name: str) -> str:
    """剥离行序号前缀与尾部括号分值; 剥离后为空则回退原值(不制造空名)。

 单一权威源: 明细/列统计/嵌入式三条产出路径共用本函数, 禁止各自实现
 (教训: 同一业务口径多处实现必然漂移)。

 依据: 33份训练语料失败归因实测——序号前缀(样本10, 20/21项)与尾部括号分值
 (样本18, 13/14项)是名称不匹配的两大来源, 且分值本身全部正确。
 剥离后同表相邻行可能身份键碰撞, 由 scoring_identity.is_identity_merge_safe
 的描述阈值护栏保证不吞行。
 """
    compact = _compact(name)
    if not compact:
        return compact
    stripped = _ORDINAL_PREFIX_RE.sub("", compact, count=1)
    stripped = _TRAILING_SCORE_SUFFIX_RE.sub("", stripped).strip()
    stripped = _BARE_TRAILING_SCORE_RE.sub("", stripped).strip()
    # 最小业务名长度护栏: 单字结果(如 "第3分" → "第")无法构成可辨识业务名, 回退 compact。
    if 0 < len(stripped) < 2:
        stripped = compact
    return stripped or compact


def _strip_layout_decoration(name: str) -> str:
    """内部别名: 保持 normalize_scoring_item_name 既有调用形态。"""
    return strip_scoring_name_decoration(name)


def _looks_polluted(name: str) -> bool:
    if any(term in name for term in _POLLUTION_TERMS):
        return True
    if len(name) > 30:
        return True
    return bool(_GROUP_ONLY_RE.match(name))


def _tail_label(rule: str) -> str:
    text = _compact(rule)
    if not text:
        return ""
    match = _TAIL_LABEL_RE.search(text)
    if not match:
        return ""
    label = match.group(1).strip("：:;；,，。")
    if not (2 <= len(label) <= 18):
        return ""
    if any(term in label for term in _BAD_TAIL_TERMS):
        return ""
    if re.fullmatch(r"\d+(?:\.\d+)?分?", label):
        return ""
    # 含分值数字的尾部片段(如"最多加2分"/"每项加2分")是计分语句残段,
    # 不是评分对象名称; 权威尾标签为业务短名, 不应携带"N分"。
    if re.search(r"\d+(?:\.\d+)?分", label):
        return ""
    return label


# 交叉引用/免责语境: 句中提及其他评分项仅为声明"不可重复计分"等约束,
# 不代表本行评分对象(: 企业业绩行因免责句被误判为复合人员业绩)。
_CROSS_REF_CLAUSE_RE = re.compile(
    r"[^。；;]*(?:不可以?重复计分|不得重复计分|不可重复|不得重复)[^。；;]*"
)


def _strip_cross_reference_clauses(text: str) -> str:
    """剔除含"不可重复计分"类免责声明的子句, 防止其中提及的他项名称污染语义匹配。"""
    return _CROSS_REF_CLAUSE_RE.sub("", text)


def _semantic_rule_label(name: str, rule: str) -> str:
    """从价格公式或复合人员业绩规则中提取稳定业务主题。

 关键词全含式匹配前必须先剔除交叉引用/免责子句:
 "企业业绩与项目经理业绩、设计负责人业绩不可以重复计分"会让
 企业业绩行同时命中"项目经理/设计负责人/业绩"三词。
 """
    text = _strip_cross_reference_clauses(_compact(rule))
    if (
        name not in {"价格", "报价评分", "评标价", "有效响应报价", "投标报价"}
        and "投标报价" in text
        and any(term in text for term in ("评标基准", "基准值"))
    ):
        return "投标报价"
    if all(term in text for term in ("项目经理", "设计负责人", "业绩")):
        return "项目经理及设计负责人业绩"
    return ""


def _leading_label(rule: str) -> str:
    """提取规则开头、首个分号前的简短主题，拒绝正文式开头。"""
    text = _compact(rule)
    match = _LEADING_LABEL_RE.match(text)
    if not match:
        return ""
    label = match.group(1)
    if any(term in label for term in _BAD_TAIL_TERMS):
        return ""
    if label.startswith(("根据", "评委", "投标人", "供应商")):
        return ""
    return label


def _compact(text: object) -> str:
    return re.sub(r"\s+", "", str(text or "")).strip("：:;；,，。")


__all__ = [
    "normalize_scoring_item_name",
    "recover_subitem_name_from_rule",
    "strip_scoring_name_decoration",
]
