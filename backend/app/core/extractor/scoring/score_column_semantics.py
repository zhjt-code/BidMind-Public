"""评分列语义共享工具。

职责: 统一短分值、权重值、合计行、序号列和满分列表头语义判定。
不做: 不判断整表类型、不提取评分项、不调用 LLM/DB。
"""

from __future__ import annotations

import re

from app.core.extractor.scoring.score_total_row_policy import (
    TOTAL_ROW_TERMS,
)
from app.core.extractor.scoring.score_total_row_policy import (
    is_total_row_strict as _is_total_row_strict,
)

SCORE_HEADER_TERMS: tuple[str, ...] = (
    "满分",
    "分值",
    "分数",
    "标准分",
    "标准分值",
    "基本分",
    "基准分",
    "最高分",
    "最高得分",
    "最高扣分",
    "得分上限",
    "分值设置",
    "配分",
    "赋分",
    "评审分",
    "评分分",
)

STRUCTURAL_SCORE_ITEM_NAME_TERMS: tuple[str, ...] = (
    "分值构成",
    "评分因素",
    "评审因素",
    "条款内容",
)
STRUCTURAL_SCORE_ITEM_NAME_EXACT_TERMS: tuple[str, ...] = (
    "合计",
)
PRICE_FORMULA_GROUP_TERMS: tuple[str, ...] = ("评标价", "报价", "价格")
PRICE_FORMULA_FACTOR_TERMS: tuple[str, ...] = ("得分计算公式", "价格得分", "报价得分")

# 表头签名词表——用于"表头锚定"的行级判定(与列判定的 SCORE_HEADER_TERMS 分离)。
# "得分"在数据列语义中是评委填写列(不作满分列词), 但作为表头签名的分值词合法(1545 三列表)。
HEADER_SCORE_TERMS: tuple[str, ...] = SCORE_HEADER_TERMS + ("得分",)
# 对象/因素列词: 长词按包含匹配; "项目/内容"过泛, 仅全等匹配(防"条款内容/单价项目"误配)。
_HEADER_ITEM_TERMS: tuple[str, ...] = (
    "评分项目",
    "评分项",
    "评分因素",
    "评审项目",
    "评审因素",
    "评分对象",
    "细分项",
    "评议内容",
    "评议因素",
    "二级评审子类",
)
_HEADER_ITEM_TERMS_EXACT: tuple[str, ...] = ("项目", "内容", "评审内容", "评分内容")
_HEADER_RULE_TERMS: tuple[str, ...] = (
    "评分标准",
    "评审标准",
    "评议标准",
    "评定标准",
    "评定内容及标准",
    "主要内容",
)
# 表头单元格是短词; 超过该长度的命中多半来自数据行长文本, 不作表头信号。
_HEADER_CELL_MAX_LEN = 10

# 负向表头语义——这些列的数字是编号/定位/计量值, 不是评分分值。
# 命中负向且无正向分值词(SCORE_HEADER_TERMS)的列, 禁止凭数值分布当分值列。
NON_SCORE_HEADER_TERMS: tuple[str, ...] = (
    "章节",
    "页码",
    "页次",
    "序号",
    "条款号",
    "编号",
    "数量",
    "单位",
    "年份",
    "月份",
    "天数",
    "期限",
    "人数",
    "次数",
    "占比",
    "百分比",
)

_MAX_SHORT_SCORE_VALUE = 200.0
_SHORT_SCORE_RE = re.compile(r"^\s*(?:满分)?\s*(\d+(?:\.\d+)?)\s*分?\s*$")
# 调整项分值格: 恒 0 的说明性评审项与 -N 扣分项; 正分值仍由 _SHORT_SCORE_RE 处理。
_ADJUSTMENT_SCORE_RE = re.compile(r"^([-−]?\d+(?:\.\d+)?)\s*分?$")
# 扣分语义词: 零分调整项含这些词即为扣分规则说明, 零分是正常形态而非分值缺失。
_DEDUCTION_SEMANTIC_TERMS: tuple[str, ...] = (
    "扣分", "扣减", "不良行为", "失信", "违约", "处罚", "黑名单", "限制采购",
)
# 区间型分值单元格: "0-30" / "0~30" / "0至30分"; 上限即该项满分。
_RANGE_SCORE_RE = re.compile(
    r"^(\d+(?:\.\d+)?)\s*[-~～—–至]\s*(\d+(?:\.\d+)?)\s*分?$"
)
# 水印噪声容忍: 噪声 token 至多 2 个汉字, 且不得含评分/数量语义字——
# 含这些字的短词(得/扣/低于/最高/每…)是评分规则片段, 解析出的数字不可作满分。
_NOISE_TOKEN_MAX_LEN = 2
_SCORE_SEMANTIC_CHARS = frozenset("分得扣加满减最每低高超共各另再倍约以上下内限")
_PURE_CJK_RE = re.compile(r"^[\u4e00-\u9fff]+$")
_WEIGHT_VALUE_RE = re.compile(r"^\s*(0?\.\d+|1\.0+)\s*$")
# 行级评分规则语义——"N分"数值分表达, 或评分动作词。
_SCORE_MENTION_RE = re.compile(r"\d+(?:\.\d+)?\s*分")
_RULE_SEMANTIC_TERMS: tuple[str, ...] = (
    "得分",
    "满分",
    "扣分",
    "不得分",
    "加分",
    "酌情",
)
# 括号内嵌分值: "实力及信誉（5分）" / "方案（6-10分）"; 区间按调用方显式允许后取上限。
_EMBEDDED_SCORE_RE = re.compile(
    r"(?P<name>[^（(]*?)\s*[（(]\s*(?P<score>[-−]?\d+(?:\.\d+)?)"
    r"(?:\s*[-~～—–至]\s*(?P<score_high>\d+(?:\.\d+)?))?\s*分\s*[）)]"
)
# 卷总分/分值构成说明, 非评分子项(避免被当括号分值项)。
_EMBEDDED_SCORE_SKIP_TERMS = ("总分", "分值构成", "合计", "满分合计")


def normalize_cell(text: object) -> str:
    """归一单元格文本，保留语义字符、去掉空白。"""
    return re.sub(r"\s+", "", str(text or ""))


def row_text(row: list[str]) -> str:
    """拼接整行文本。"""
    return "".join(normalize_cell(cell) for cell in row)


def is_total_row(row: list[str]) -> bool:
    """是否为合计/结果汇总行（委托 score_total_row_policy， 精确化）。

 仅短结构单元格命中，长规则正文中的同名词不触发。
 """
    return _is_total_row_strict(row)


def is_structural_score_item_name(text: object) -> bool:
    """名称格是否只是表头/分值构成等结构词, 而非真实评分对象。"""
    compact = normalize_cell(text)
    return compact in STRUCTURAL_SCORE_ITEM_NAME_EXACT_TERMS or any(
        term in compact for term in STRUCTURAL_SCORE_ITEM_NAME_TERMS
    )


def is_price_formula_factor(group_text: object, factor_text: object) -> bool:
    """canonical行是否为价格/报价公式行。"""
    group = normalize_cell(group_text)
    factor = normalize_cell(factor_text)
    return any(term in group for term in PRICE_FORMULA_GROUP_TERMS) and any(
        term in factor for term in PRICE_FORMULA_FACTOR_TERMS
    )


def parse_short_score(text: object) -> float | None:
    """只解析短分值单元格，不从长评分规则正文猜数字。"""
    matched = _SHORT_SCORE_RE.match(normalize_cell(text))
    if not matched:
        return None
    try:
        value = float(matched.group(1))
    except (TypeError, ValueError):
        return None
    return value if 0 < value <= _MAX_SHORT_SCORE_VALUE else None


def parse_short_score_tolerant(text: object) -> float | None:
    """短分值解析 + PDF 水印噪声容忍。

 真实失效模式(项目1584): 页面水印("日照市公共资源交易…")字符穿透进分值单元格,
 形成 `20分 照` / `日 18分`, 严格短分值正则整格失配 → 该评分行被静默丢弃。

 容忍规则(从严, 不破坏 "禁止从规则正文猜数字"红线):
 按空白切 token 后, 恰好一个 token 是合法短分值, 其余 token 均为"不含数字、
 不含评分/数量语义字(得/扣/低/高/每…)的 1-2 字孤立汉字"才取该分值;
 出现两个分值 token(歧义)或任何语义字样 token 一律拒绝。
 """
    value = parse_short_score(text)
    if value is not None:
        return value
    tokens = [tok for tok in re.split(r"\s+", str(text or "")) if tok]
    if len(tokens) < 2:
        return None
    score_values: list[float] = []
    for tok in tokens:
        tok_value = parse_short_score(tok)
        if tok_value is not None:
            score_values.append(tok_value)
            continue
        compact = normalize_cell(tok)
        if not compact:
            continue
        if (
            len(compact) > _NOISE_TOKEN_MAX_LEN
            or not _PURE_CJK_RE.match(compact)
            or any(ch in _SCORE_SEMANTIC_CHARS for ch in compact)
        ):
            return None
    if len(score_values) != 1:
        return None
    return score_values[0]


def parse_score_range_max(text: object) -> float | None:
    """解析区间型分值单元格取上限为满分, 如 `0-30`→30、`0至18分`→18。

 约束: 上限须大于下限且 0<上限<=200, 拦截年份区间(2023-2025)与页码区间等非分值数字。
 """
    matched = _RANGE_SCORE_RE.match(normalize_cell(text))
    if not matched:
        return None
    try:
        low = float(matched.group(1))
        high = float(matched.group(2))
    except (TypeError, ValueError):
        return None
    if high <= low or low < 0:
        return None
    return high if 0 < high <= _MAX_SHORT_SCORE_VALUE else None


def parse_adjustment_score(text: object) -> float | None:
    """解析"调整项"分值格: `0` / `0.0` / `-3` / `-3分`。

 与 parse_short_score 互补——后者按设计只认正分值(基础分), 但真实评分表存在
 分值恒为 0 的说明性评审项(失信分/不良行为处理)与负分扣分项, 它们是评审事实,
 不得因"非正分值"被静默丢弃; 上限沿用短分值口径, 防把编号/金额当调整分。
 """
    compact = normalize_cell(text).replace("−", "-")
    matched = _ADJUSTMENT_SCORE_RE.match(compact)
    if not matched:
        return None
    try:
        value = float(matched.group(1))
    except (TypeError, ValueError):
        return None
    return value if -_MAX_SHORT_SCORE_VALUE <= value <= 0 else None


def classify_adjustment_fact_type(score: float, text: object) -> str:
    """调整项 fact_type 判定: 负分恒为 penalty; 零分按规则语义区分。

 根因: 零分调整项若一律标 base_score, 会命中"base_score 零权重=评分缺失"门禁
 (scoring_scope_validator/_MISSING_SCORE_FACT_TYPES)而误报 needs_review——
 但"失信分/不良行为处理"这类恒0项本身是扣分规则说明, 零分是其正常形态而非缺失。
 判据取规则文本中的扣分语义词, 无扣分语义的零分项仍按 base_score 保留门禁保护。
 """
    if score < 0:
        return "penalty"
    compact = normalize_cell(text)
    return "penalty" if any(term in compact for term in _DEDUCTION_SEMANTIC_TERMS) else "base_score"


def parse_weight_value(text: object) -> float | None:
    """解析权重单元格的小数表达，如 0.40 / 1.0。"""
    matched = _WEIGHT_VALUE_RE.match(normalize_cell(text))
    if not matched:
        return None
    try:
        value = float(matched.group(1))
    except (TypeError, ValueError):
        return None
    return round(value, 4) if 0 < value <= 1 else None


def find_embedded_scores(text: object, *, allow_ranges: bool = False) -> list[tuple[str, float]]:
    """提取单元格内 `对象名（N分）` 括号内嵌分值; 跳过"总分/分值构成"等卷说明。

    返回 [(对象名, 分值)]; 对象名去空格, 分值 0<score<=上限。用于识别无独立满分列、
    分值嵌在评审因素文本中的评分表(如 1521 商务/技术服务/报价层级评分)。
    """
    normalized = normalize_cell(text)
    if not normalized:
        return []
    out: list[tuple[str, float]] = []
    for match in _EMBEDDED_SCORE_RE.finditer(normalized):
        name = match.group("name").strip("：:;；,，。")
        if not name or any(term in name for term in _EMBEDDED_SCORE_SKIP_TERMS):
            continue
        if match.group("score_high") is not None and not allow_ranges:
            continue
        try:
            score = float(match.group("score_high") or match.group("score"))
        except (TypeError, ValueError):
            continue
        if 0 < score <= _MAX_SHORT_SCORE_VALUE:
            out.append((name, score))
    return out


def has_embedded_score(text: object) -> bool:
    """单元格是否含 `对象名（N分）` 括号内嵌分值。"""
    return bool(find_embedded_scores(text))


# 纯扣分区间(阶段3, 样本9诚信评价形态): "对象名（-30-0分）"——上限恒为0, 下限为负。
# 混合区间(-4-4分)不在此匹配(语义为加扣混合, 需候选模型支持后再建模)。
_EMBEDDED_PENALTY_RE = re.compile(
    r"(?P<name>[^（()）]{2,40})[（(]\s*[-−]\s*(?P<floor>\d+(?:\.\d+)?)\s*[-~～—至]\s*0\s*分\s*[）)]"
)


def find_embedded_penalties(text: object) -> list[tuple[str, float]]:
    """提取单元格内 `对象名（-N-0分）` 纯扣分区间; 返回 [(对象名, 最大扣分N)]。

    扣分项不进入基础满分(fact_type=penalty, score_weight=0), 仅作为调整项建模;
    禁止把 -30-0 误读为 30 分正项(方案4.6 红线)。
    """
    normalized = normalize_cell(text)
    if not normalized:
        return []
    out: list[tuple[str, float]] = []
    for match in _EMBEDDED_PENALTY_RE.finditer(normalized):
        name = match.group("name").strip("：:;；,，。")
        if not name:
            continue
        try:
            floor = float(match.group("floor"))
        except (TypeError, ValueError):
            continue
        if 0 < floor <= _MAX_SHORT_SCORE_VALUE:
            out.append((name, floor))
    return out


def is_scoring_header_row(row: list[str]) -> bool:
    """行是否具备评分表表头签名（表头锚定判据）。

 职责: 纯文字签名判定——同时命中(对象/因素列词 AND 标准/内容列词)或
 (对象/因素列词 AND 表头分值词)即为表头行; 与行号/词表位置解耦, 供全行扫描。
 不做: 不验证数据分布(装配器双向验证职责)、不判定整表类型(detector 职责)。
 """
    has_item = False
    has_rule = False
    has_score = False
    for cell in row:
        compact = normalize_cell(cell)
        if not compact or len(compact) > _HEADER_CELL_MAX_LEN:
            continue
        if not has_item and (
            any(term in compact for term in _HEADER_ITEM_TERMS)
            or compact in _HEADER_ITEM_TERMS_EXACT
        ):
            has_item = True
        if not has_rule and any(term in compact for term in _HEADER_RULE_TERMS):
            has_rule = True
        if not has_score and any(term in compact for term in HEADER_SCORE_TERMS):
            has_score = True
    return has_item and (has_rule or has_score)


def is_non_score_header(text: object) -> bool:
    """表头是否为负向语义(章节/页码/编号/数量等), 其数字不是评分分值。

 职责: 提供分值列判定的负向一票否决信号; 表头同时含正向分值词(如"权重分值")时正向优先。
 不做: 不判断整列数值分布(调用方职责)。
 """
    normalized = normalize_cell(text)
    if not normalized:
        return False
    if any(term in normalized for term in SCORE_HEADER_TERMS):
        return False
    return any(term in normalized for term in NON_SCORE_HEADER_TERMS)


def has_scoring_rule_semantics(text: object) -> bool:
    """文本是否含评分规则语义——"N分"数值分或得分/扣分/酌情等评分动作词。

 职责: 机器判定"评分对象+分值+评分规则"三元关系中的第三元, 供 detector 分值列识别、
 结构信号行统计与列统计解析共用, 拦截"编号/章节/数量"数字列误判为分值。
 不做: 不判断分值本身(parse_short_score 职责)、不判断表整体类型。
 """
    normalized = normalize_cell(text)
    if not normalized:
        return False
    if _SCORE_MENTION_RE.search(normalized):
        return True
    return any(term in normalized for term in _RULE_SEMANTIC_TERMS)


_CHINESE_ORDINALS: frozenset[str] = frozenset("一二三四五六七八九十")


def is_ordinal_column(rows: list[list[str]], idx: int) -> bool:
    """序号/条款号列是行标识，不是满分列。

    检测范围: 阿拉伯数字序号(1,2,3)与中文序号(一/二/三/五等)。
    """
    header_text = "".join(normalize_cell(_cell(row, idx)) for row in rows[:3])
    if any(term in header_text for term in ("序号", "条款号", "编号")):
        return True
    # 中文序号检测（归档 /Task2: holdout31 "评审项目"列值为一/二/五，
    # 无表头词面线索，仅凭阿拉伯数字判据会漏判，导致序号被当作评分项名）
    chinese_hits = sum(
        1
        for row in rows[1:8]
        if normalize_cell(_cell(row, idx)).rstrip("、。") in _CHINESE_ORDINALS
    )
    if chinese_hits >= 2:
        return True
    # 阿拉伯序号检测
    values: list[int] = []
    for row in rows[1:8]:
        text = normalize_cell(_cell(row, idx))
        if not text.isdigit():
            continue
        try:
            values.append(int(text))
        except ValueError:
            continue
    return len(values) >= 3 and values[:3] == [1, 2, 3]


def _cell(row: list[str], idx: int) -> str:
    return str(row[idx] or "").strip() if 0 <= idx < len(row) else ""


__all__ = [
    "HEADER_SCORE_TERMS",
    "NON_SCORE_HEADER_TERMS",
    "SCORE_HEADER_TERMS",
    "TOTAL_ROW_TERMS",
    "is_scoring_header_row",
    "find_embedded_scores",
    "has_embedded_score",
    "has_scoring_rule_semantics",
    "is_non_score_header",
    "is_ordinal_column",
    "is_total_row",
    "classify_adjustment_fact_type",
    "normalize_cell",
    "parse_adjustment_score",
    "parse_score_range_max",
    "parse_short_score",
    "parse_short_score_tolerant",
    "parse_weight_value",
    "row_text",
]
