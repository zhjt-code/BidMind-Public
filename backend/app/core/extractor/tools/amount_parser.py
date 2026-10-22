"""确定性中文货币金额解析器，用于政府招投标审查。

所有函数均为纯函数，严禁 DB / 网络调用。
精度使用 Decimal，所有货币值以人民币"元"为基准输出。

pycnnum 行为说明
-----------------
pycnnum 对"系数 + 万"组合的处理与口语习惯不一致：
- cn2num("三百五十万") → 30_500_000（错）期望 3_500_000
- cn2num("壹仟贰佰万") → 1_002_000_000（错）期望 12_000_000
- cn2num("三亿五千万") → 350_000_000（正确）

因此，当末尾单位为"万/万元"且系数中不含"亿"时，
先剥离"万/万元"后缀、对系数单独调用 cn2num，再乘以 10000。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Literal

from pycnnum import cn2num as _pycnnum  # type: ignore[import-untyped]

# ---------------------------------------------------------------------------
# 公共数据结构
# ---------------------------------------------------------------------------

_UnitLabel = Literal["yuan", "wan_yuan", "yi_yuan"]
_Confidence = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class AmountVerdict:
    """金额解析结果。"""

    canonical_value: Decimal | None = None
    """标准化后的人民币元值；无法解析时为 None。"""

    unit: _UnitLabel | None = None
    """原文表达单位：yuan=元/千元/百万，wan_yuan=万/万元，yi_yuan=亿/亿元。"""

    confidence: _Confidence = "low"
    """解析置信度：high=阿拉伯数字+明确单位，medium=中文数字/区间取值，low=回退/无单位。"""

    parse_trace: list[str] = field(default_factory=list)
    """解析过程日志，供调试排查使用。"""


# ---------------------------------------------------------------------------
# 内部常量
# ---------------------------------------------------------------------------

_WAN: Decimal = Decimal("10000")
_YI: Decimal = Decimal("100000000")

# (乘数, 单位标签)  —— 按键长度降序排列，确保正则优先匹配最长单位
_UNIT_MAP: dict[str, tuple[Decimal, _UnitLabel]] = {
    "亿元": (_YI, "yi_yuan"),
    "亿": (_YI, "yi_yuan"),
    "百万": (Decimal("1000000"), "yuan"),
    "万元": (_WAN, "wan_yuan"),
    "万": (_WAN, "wan_yuan"),
    "千元": (Decimal("1000"), "yuan"),
    "元": (Decimal("1"), "yuan"),
    "圆": (Decimal("1"), "yuan"),
}

# 正则：阿拉伯数字 + 可选货币单位（最长优先）
_UNIT_RE_PART: str = "|".join(
    re.escape(u) for u in sorted(_UNIT_MAP, key=len, reverse=True)
)
_ARABIC_AMOUNT_RE: re.Pattern[str] = re.compile(
    rf"(?<!\d)(\d[\d,.]*)[ \t]*({_UNIT_RE_PART})"
)

# 区间表达式（仅处理阿拉伯数字区间）：500-800万元 / 300~500万
_RANGE_RE: re.Pattern[str] = re.compile(
    r"(\d[\d,.]*)[ \t]*[-\u2013\u2014~\uff5e][ \t]*\d[\d,.]*"
)

# 括号注记：(含税) / （不含税） 等
_PAREN_RE: re.Pattern[str] = re.compile(r"[\uff08(][^\uff09)]*[\uff09)]")

# 尾部噪声：整、正
_SUFFIX_NOISE_RE: re.Pattern[str] = re.compile(r"[整正]\s*$")

# 中文数字字符集（用于判断纯中文数字串）
_CN_NUMERAL_CHARS: frozenset[str] = frozenset(
    "零一二三四五六七八九十百千两○〇"
    "壹贰叁肆伍陆柒捌玖拾佰仟"
    "万亿点"
)


# ---------------------------------------------------------------------------
# 内部工具函数
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    """将全角字符转为半角，去除首尾空白。"""
    buf: list[str] = []
    for ch in text:
        cp = ord(ch)
        if 0xFF01 <= cp <= 0xFF5E:
            buf.append(chr(cp - 0xFEE0))
        elif cp == 0x3000:
            buf.append(" ")
        else:
            buf.append(ch)
    return "".join(buf).strip()


def _remove_thousands_sep(text: str) -> str:
    """去除千位分隔逗号：1,200,000 → 1200000。"""
    return re.sub(r"(?<=\d),(?=\d)", "", text)


def _to_decimal(s: str) -> Decimal | None:
    """安全解析阿拉伯数字字符串为 Decimal。"""
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


_STANDALONE_SCALE: dict[str, int] = {
    "百": 100, "佰": 100, "千": 1000, "仟": 1000,
}

_ZERO_CHARS: frozenset[str] = frozenset("零〇０0")


def _cn2num_safe(text: str) -> Decimal | None:
    """安全调用 pycnnum.cn2num，任何异常均返回 None。

    pycnnum 对无效输入（如 ``百``、``若干``）返回 0 而非抛异常，
    因此需额外校验：结果为 0 时仅当输入表示真正的零才接受。
    """
    if not text:
        return None
    stripped = text.strip()
    if not stripped:
        return None
    if stripped in _STANDALONE_SCALE:
        return Decimal(str(_STANDALONE_SCALE[stripped]))
    try:
        result = _pycnnum(stripped)
        if result == 0 and not all(c in _ZERO_CHARS for c in stripped):
            return None
        return Decimal(str(result))
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# 中文数字内部解析器
# ---------------------------------------------------------------------------


def _parse_cn_coeff_with_scale(
    coeff: str,
    trace: list[str],
) -> tuple[Decimal, _UnitLabel] | None:
    """
    解析可能包含"亿/万"量级标记的中文数字系数。
    在已剥离终端货币后缀（元/圆）之后调用。

    例：
    - "三亿五千万" → 350_000_000, "yuan"（pycnnum 可正确处理含亿复合数）
    - "三百五十万"  → 3_500_000,   "wan_yuan"（剥离万后 cn2num("三百五十")=350）
    - "叁仟"        → 3_000,        "yuan"
    """
    if not coeff:
        return None

    # 1. 系数以"亿"结尾：e.g., "三亿" → 3 × 1e8
    if coeff.endswith("亿"):
        inner = coeff[:-1]
        val = _cn2num_safe(inner) if inner else Decimal("1")
        if val is not None:
            trace.append(f"CN inner: {inner!r} × 亿")
            return val * _YI, "yi_yuan"

    # 2. 系数含有嵌入"亿"（如 "三亿五千万"）：pycnnum 可正确处理
    if "亿" in coeff:
        val = _cn2num_safe(coeff)
        if val is not None:
            trace.append(f"CN inner pycnnum 亿: {coeff!r} → {val}")
            return val, "yuan"

    # 3. 系数以"万"结尾且无"亿"：剥离"万"后对系数调用 cn2num
    #    pycnnum("三百五十") = 350，避免 pycnnum("三百五十万") = 30_500_000 的错误
    if coeff.endswith("万"):
        inner = coeff[:-1]
        val = _cn2num_safe(inner) if inner else Decimal("1")
        if val is not None:
            trace.append(f"CN inner: {inner!r} × 万")
            return val * _WAN, "wan_yuan"

    # 4. 纯数字（无量级标记）
    val = _cn2num_safe(coeff)
    if val is not None:
        trace.append(f"CN inner simple: {coeff!r} → {val}")
        return val, "yuan"

    return None


def _try_chinese(
    text: str,
    trace: list[str],
) -> tuple[Decimal, _UnitLabel, _Confidence] | None:
    """
    尝试将文本解析为纯中文数字金额。

    返回 (人民币元值, 单位标签, 置信度) 或 None。

    解析策略（按优先级）：
    1. 末尾为"亿元/亿"   → 剥离单位，cn2num 系数 × 1e8
    2. 末尾为"百万"       → 剥离单位，cn2num 系数 × 1e6
    3. 末尾为"千元"且有系数 → 剥离单位，cn2num 系数 × 1e3
    4. 末尾为"万元/万"且系数无"亿" → 剥离单位，cn2num 系数 × 1e4
    5. 末尾为"元/圆"     → 剥离货币后缀，递归解析内部含量级标记的系数
    6. 无后缀兜底        → pycnnum 直接解析全文（置信度=low）
    """
    if not any(c in _CN_NUMERAL_CHARS for c in text):
        return None

    # 1. 亿元 / 亿
    for unit_str in ("亿元", "亿"):
        if text.endswith(unit_str):
            coeff = text[: -len(unit_str)]
            val = _cn2num_safe(coeff) if coeff else Decimal("1")
            if val is not None:
                trace.append(f"CN unit={unit_str!r} coeff={coeff!r}")
                return val * _YI, "yi_yuan", "medium"

    # 2. 百万
    if text.endswith("百万"):
        coeff = text[:-2]
        val = _cn2num_safe(coeff) if coeff else Decimal("1")
        if val is not None:
            trace.append(f"CN unit='百万' coeff={coeff!r}")
            return val * Decimal("1000000"), "yuan", "medium"

    # 3. 千元（需要有系数；"千元"独立时由步骤 5 的"元"分支处理）
    if text.endswith("千元"):
        coeff = text[:-2]
        if coeff:
            val = _cn2num_safe(coeff)
            if val is not None:
                trace.append(f"CN unit='千元' coeff={coeff!r}")
                return val * Decimal("1000"), "yuan", "medium"

    # 4. 万元 / 万（仅当系数中无"亿"时才剥离万作为单位）
    for unit_str in ("万元", "万"):
        if text.endswith(unit_str):
            coeff = text[: -len(unit_str)]
            if coeff and "亿" not in coeff:
                val = _cn2num_safe(coeff)
                if val is not None:
                    trace.append(f"CN unit={unit_str!r} coeff={coeff!r}")
                    return val * _WAN, "wan_yuan", "medium"
            # 若系数含亿（如"三亿五千万元"中误触"万元"），
            # break 跳出，交由步骤 5 的"元"分支处理
            break

    # 5. 元 / 圆 — 剥离货币后缀，递归解析含量级标记的系数
    for unit_str in ("元", "圆"):
        if text.endswith(unit_str):
            coeff = text[:-1]
            result = _parse_cn_coeff_with_scale(coeff, trace)
            if result is not None:
                val, unit_label = result
                trace.append(f"CN unit={unit_str!r}")
                return val, unit_label, "medium"

    # 6. 无后缀兜底（pycnnum 直接处理，置信度低）
    val = _cn2num_safe(text)
    if val is not None:
        trace.append(f"CN no-unit pycnnum: {text!r} → {val}")
        return val, "yuan", "low"

    return None


# ---------------------------------------------------------------------------
# 阿拉伯数字解析器
# ---------------------------------------------------------------------------


def _try_arabic(
    text: str,
    trace: list[str],
) -> tuple[Decimal, _UnitLabel, bool] | None:
    """
    尝试将文本解析为阿拉伯数字金额（包含混合格式如"500万元"）。

    返回 (人民币元值, 单位标签, 是否为区间取值) 或 None。
    区间取值时取下界，例："500-800万元" → 5_000_000, is_range=True。
    """
    # 检测区间：500-800万元 / 300~500万
    rng = _RANGE_RE.search(text)
    is_range = rng is not None
    working = text
    if is_range:
        lower = rng.group(1)
        working = working[: rng.start()] + lower + working[rng.end():]
        trace.append(f"range: 取下界 → {working!r}")

    clean = _remove_thousands_sep(working)
    m = _ARABIC_AMOUNT_RE.search(clean)
    if not m:
        return None

    num_str = m.group(1)
    unit_str = m.group(2) if m.lastindex and m.lastindex >= 2 else ""
    if not unit_str:
        return None  # 无单位，无法确定元值

    num_val = _to_decimal(num_str)
    if num_val is None:
        return None

    multiplier, unit_label = _UNIT_MAP[unit_str]
    canonical = num_val * multiplier
    trace.append(f"arabic: {num_str!r} × {unit_str!r} = {canonical}")
    return canonical, unit_label, is_range


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------


def parse_amount(text: str) -> AmountVerdict:
    """
    从中文文本中解析货币金额，返回以人民币"元"为基准的标准化结果。

    解析步骤：
    1. 全角转半角，去除首尾空白
    2. 剥离括号注记（含税）等，去除尾部"整/正"
    3. 尝试阿拉伯数字 + 单位正则（包含区间下界提取）
    4. 尝试纯中文数字解析（含量级标记剥离策略）
    5. 无法解析时返回 canonical_value=None, confidence="low"

    Args:
        text: 来自招投标文件的原始金额字符串。

    Returns:
        AmountVerdict：包含以元为单位的 Decimal 值、单位标签、
        置信度及解析追踪日志。

    Examples:
        >>> parse_amount("500万元").canonical_value
        Decimal('5000000')
        >>> parse_amount("叁佰伍拾万元整").canonical_value
        Decimal('3500000')
        >>> parse_amount("3.5亿").canonical_value
        Decimal('350000000.0')
    """
    trace: list[str] = []

    # Step 1: 全角→半角，去空白
    normalized = _normalize(text)
    trace.append(f"input={text!r} normalized={normalized!r}")

    # Step 2: 剥离括号注记 & 尾部整/正
    cleaned = _PAREN_RE.sub("", normalized).strip()
    if cleaned != normalized:
        trace.append(f"strip parens → {cleaned!r}")
    cleaned = _SUFFIX_NOISE_RE.sub("", cleaned).strip()

    # Step 3: 阿拉伯数字解析
    arabic_result = _try_arabic(cleaned, trace)
    if arabic_result is not None:
        val, unit, is_range = arabic_result
        confidence: _Confidence = "medium" if is_range else "high"
        return AmountVerdict(
            canonical_value=val,
            unit=unit,
            confidence=confidence,
            parse_trace=trace,
        )

    # Step 4: 纯中文数字解析
    cn_result = _try_chinese(cleaned, trace)
    if cn_result is not None:
        val, unit, confidence = cn_result
        return AmountVerdict(
            canonical_value=val,
            unit=unit,
            confidence=confidence,
            parse_trace=trace,
        )

    # Step 5: 无法解析
    trace.append("unparseable")
    return AmountVerdict(parse_trace=trace)


def compare_amount(
    requirement: str,
    response: str,
    *,
    tolerance_pct: float = 0.0,
) -> Literal["meets", "below", "above", "unparseable"]:
    """
    比较投标响应金额与招标要求金额的大小关系。

    Args:
        requirement: 招标要求金额字符串，例："500万元"。
        response:    投标响应金额字符串，例："600万元"。
        tolerance_pct: 容差百分比（默认 0.0）。
            设为 5.0 时，响应值 >= 要求值 × 0.95 即视为满足。

    Returns:
        - ``"meets"``      : 响应值 ∈ [要求值×(1-容差), 要求值]，满足要求。
        - ``"above"``      : 响应值 > 要求值，超出上限（如保证金封顶场景）。
        - ``"below"``      : 响应值 < 阈值，不满足要求。
        - ``"unparseable"``: 任一方无法解析。

    Examples:
        >>> compare_amount("500万元", "600万元")
        'above'
        >>> compare_amount("500万元", "500万元")
        'meets'
        >>> compare_amount("500万元", "400万元")
        'below'
    """
    req_v = parse_amount(requirement)
    resp_v = parse_amount(response)

    if req_v.canonical_value is None or resp_v.canonical_value is None:
        return "unparseable"

    req_val = req_v.canonical_value
    resp_val = resp_v.canonical_value
    threshold = req_val * Decimal(str(1 - tolerance_pct / 100))

    if resp_val > req_val:
        return "above"
    if resp_val >= threshold:
        return "meets"
    return "below"
