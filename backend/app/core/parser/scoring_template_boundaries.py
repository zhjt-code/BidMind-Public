"""模板编码边界检测（Task 5，第三轮修订—两阶段同源锚点）。

职责: 从页面文本中识别标题行里的模板编码锚点；提供两阶段接口供不同消费方使用；
 为每个编码生成稳定 boundary_id。
不做: 不解析评分项、不访问 DB/LLM、不修改 ParsedDocument；不作跨页语境推断。

两阶段同源锚点模型（A1/A2 修订）:
- extract_template_anchors(page_texts): 第一阶段——从所有页面提取全量锚点，不受开关影响；
 has_multi_template_scheme_signal 使用此函数；同页多锚点原样保留（含同页多个），
 交既有 table_stitcher 序号重启机制处理表内方案切分。
- detect_template_boundaries(page_texts): 第二阶段——仅选"该页恰好1个锚点"的安全页边界，
 受 SCORING_TEMPLATE_BOUNDARY_ENABLED 开关控制；歧义页 loguru warning 并跳过页边界，
 但其锚点仍计入 has_multi_template_scheme_signal 全量统计。

判据设计（A3 修订—无硬性冒号要求）:
- 编码形态：大写字母开头(2+) + 连接符结构（JS-FWTY/JS-GCZCB）
 OR 字母前缀 + 数字后缀(>=2位, FWJS05/FWJS16)
- 分隔符可选：允许冒号或编码后直接中文标题（如 FWJS16服务技术详评模板）
- 同行 40 字内须含精确模板语境词之一：
 详评模板|评分模板|评分细则|评审细则|技术详评|商务详评
 （排除 "认证细则"、"通用细则" 等非评分模板上下文）
- 负向拒绝（仅作附加）：同行含 型号|规格|设备选型|处理器|产品型号 时拒绝
- 前导：行首可有 "N." / "附件N " / "模板N：" / "细则N：" 等合理前缀

负例说明:
- TEL/ESG/CMMI/CISP：纯大写字母，无连接符，无数字后缀 → 不命中编码形态
- PLC-S7300/CPU-I7：行内无"详评模板/评分细则"等精确词 → 正向检查不通过
- ISO9001/GB9001：行内通常无精确模板词 → 正向检查不通过
- 全文只有 1 个不同编码 → 返回空列表
- 同页出现 2+ 个不同编码锚点 → detect_template_boundaries 跳过（fail-visible），
 但 extract_template_anchors 仍返回
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from loguru import logger

# ---------------------------------------------------------------------------
# 正则：模板编码锚点（A3 修订—无硬性冒号）
# ---------------------------------------------------------------------------
#
# 前缀 (可选):
#   - 行首空白
#   - 数字序号 "N." / "N、"
#   - 附件编号 "附件N "
#   - 模板/细则标签 "模板N：" / "细则一："
#
# 编码 (两种形态之一):
#   A) 连接符结构: [A-Z]{2,} + (-[A-Z0-9]+)+ —— 如 JS-FWTY / JS-GCKYSJ01
#   B) 数字后缀:   [A-Z]{2,} + \d{2,}        —— 如 FWJS05 / FWJS16
#   两种均要求 [A-Z]{2,} 之后紧跟连接符或数字（排除纯字母 CMMI/TEL/ESG）
#
# 分隔符: 可选（冒号或直接中文，0-2 字符范围内）
#
# 语境: 同行 40 字内出现精确模板术语之一
#       详评模板|评分模板|评分细则|评审细则|技术详评|商务详评
#
_ANCHOR_CODE_RE = re.compile(
    r"(?m)"                                                          # MULTILINE
    r"^[ \t]*"                                                       # 行首空白
    r"(?:\d{1,2}[.．、][ \t]*"                                      # N. 序号前缀
    r"|附件[ \t]*\d+[ \t]+"                                          # 附件N 前缀
    r"|(?:模板|细则)[一二三四五六七八九十\d]+[：:][ \t]*)?"           # 模板/细则N: 前缀
    r"([A-Z]{2,}(?:-[A-Z0-9]+)+|[A-Z]{2,}\d{2,})"                  # 编码本体（两种形态）
    r"(?![A-Z0-9\-])"                                               # 不被更长串吞噬
    r"[：:\s]{0,2}"                                                  # 可选分隔符/空白
    r"[^\n]{0,40}"                                                   # 同行 40 字内
    r"(?:详评模板|评分模板|评分细则|评审细则|技术详评|商务详评)",      # 精确模板语境
)

# 负向：同行含明确型号语境时拒绝（仅作附加保险）
_MODEL_CONTEXT_RE = re.compile(r"型号|规格|设备选型|处理器|产品型号")

_MIN_ANCHORS = 2  # 文档级至少 2 个不同编码才激活边界检测


@dataclass(frozen=True)
class TemplateBoundary:
    """单页模板编码锚点（Task 5）。

 职责: 记录哪一页以哪个编码标识了一个独立评分模板/细则的起始。
 不做: 不记录页范围结束位置（由 scoring_assembly_scopes 计算交集）。
 """

    page_num: int
    template_code: str  # 如 "JS-GCKYSJ01"、"FWSW01"


# ---------------------------------------------------------------------------
# 内部：从单行/单页文本提取编码（A3）
# ---------------------------------------------------------------------------


def _extract_codes_from_text(text: str) -> list[str]:
    """从页面文本（多行）中提取所有满足条件的模板编码。

    同一行若含型号上下文则跳过该行（负向附加）。
    """
    results: list[str] = []
    for m in _ANCHOR_CODE_RE.finditer(text):
        code = m.group(1)
        # 提取匹配所在行，用于负向型号语境检查
        line_start = text.rfind("\n", 0, m.start()) + 1
        line_end = text.find("\n", m.end())
        if line_end == -1:
            line_end = len(text)
        line = text[line_start:line_end]
        if _MODEL_CONTEXT_RE.search(line):
            continue
        results.append(code)
    return results


# ---------------------------------------------------------------------------
# 第一阶段：全量锚点（A1 — 不受开关影响）
# ---------------------------------------------------------------------------


def extract_template_anchors(
    page_texts: list[tuple[int, str]],
) -> dict[int, list[str]]:
    """从 (page_num, text) 序列中提取所有模板编码锚点（A1 — 不受开关影响）。

    返回 {page_num: [code, ...]} 字典；同页多个编码均保留（含歧义页）。
    用途: has_multi_template_scheme_signal 使用此函数统计全量不同编码数；
          detect_template_boundaries 使用此函数后再筛选安全页。
    不做: 不检查开关、不过滤歧义页、不做全文最小 2 编码判断。
    """
    result: dict[int, list[str]] = {}
    seen_pages: set[int] = set()
    for page_num, text in page_texts:
        if not text or page_num in seen_pages:
            continue
        seen_pages.add(page_num)
        codes = _extract_codes_from_text(text)
        if codes:
            result[page_num] = list(dict.fromkeys(codes))  # 保序去重（同页重复行）
    return result


# ---------------------------------------------------------------------------
# 第二阶段：安全页边界（A2 — 受开关影响）
# ---------------------------------------------------------------------------


def detect_template_boundaries(
    page_texts: list[tuple[int, str]],
) -> list[TemplateBoundary]:
    """从全量锚点中选"该页恰好1个锚点"的安全页边界（A2 — 受开关影响）。

    返回规则:
    - 配置开关关闭（SCORING_TEMPLATE_BOUNDARY_ENABLED=False）→ 返回空列表；
    - 全量不同编码 < 2 → 返回空列表；
    - 同页出现 2+ 个不同编码：该页跳过，loguru WARNING（fail-visible）；
      但其锚点仍计入 extract_template_anchors 全量统计；
    - 同一编码在多页出现：只保留首次出现页（C2 稳定性）；
    - 返回按 page_num 升序，boundary_id = tb:{code}（不含页码，C2）。
    """
    # 检查开关（延迟导入避免循环）
    if not bool(getattr(settings, "SCORING_TEMPLATE_BOUNDARY_ENABLED", True)):
        return []

    per_page = extract_template_anchors(page_texts)
    if not per_page:
        return []

    # 全量不同编码数检查
    all_codes = {code for codes in per_page.values() for code in codes}
    if len(all_codes) < _MIN_ANCHORS:
        return []

    # A2: 筛选安全页（恰好1个不同编码）；歧义页 warning 跳过
    safe_per_page: dict[int, str] = {}
    for page_num in sorted(per_page):
        codes = per_page[page_num]
        unique = list(dict.fromkeys(codes))
        if len(unique) > 1:
            logger.warning(
                "[模板边界] p{} 出现 {} 个不同编码锚点 ({})，无法安全页级切分，跳过该页。",
                page_num,
                len(unique),
                ", ".join(unique),
            )
        else:
            safe_per_page[page_num] = unique[0]

    if len(set(safe_per_page.values())) < _MIN_ANCHORS:
        return []

    # C2: 同一编码只保留首次出现页
    first_page_per_code: dict[str, int] = {}
    for page_num in sorted(safe_per_page):
        code = safe_per_page[page_num]
        if code not in first_page_per_code:
            first_page_per_code[code] = page_num

    return sorted(
        [
            TemplateBoundary(page_num=pn, template_code=code)
            for code, pn in first_page_per_code.items()
        ],
        key=lambda b: b.page_num,
    )


# ---------------------------------------------------------------------------
# 辅助函数（M2：从 scoring_chunk_builder 移入）
# ---------------------------------------------------------------------------


def detect_template_boundaries_from_sections(
    pages_by_num: dict,
    sections: list,
) -> list[TemplateBoundary]:
    """从评分区段页文本中检测模板边界（M2，从 scoring_chunk_builder 移入）。

    职责: 收集各区段页面文本后调用 detect_template_boundaries。
    不做: 不修改区段列表、不访问 DB/LLM。
    """
    seen: set[int] = set()
    page_texts: list[tuple[int, str]] = []
    for sec in sections:
        for pn in range(sec.page_start, sec.page_end + 1):
            if pn in seen:
                continue
            seen.add(pn)
            page = pages_by_num.get(pn)
            if page:
                page_texts.append((pn, getattr(page, "text", "") or ""))
    return detect_template_boundaries(page_texts)


def boundary_id_for_page(boundaries: list[TemplateBoundary], page_num: int) -> str | None:
    """找 page_num 所属的最近前置模板边界，返回稳定 boundary_id（C2/I5 单源）。

    职责: 把「page_num 对应哪个模板起点」单点收口，稳定 ID = tb:{code}（无页码）。
    不做: 不修改 boundaries 列表。
    """
    prev: TemplateBoundary | None = None
    for b in sorted(boundaries, key=lambda x: x.page_num):
        if b.page_num <= page_num:
            prev = b
        else:
            break
    if prev is None:
        return None
    return f"tb:{prev.template_code}"


# ---------------------------------------------------------------------------
# 延迟导入 settings（避免循环；I3 通过 getattr 读取）
# ---------------------------------------------------------------------------
try:
    from app.core.config import settings
except Exception:  # pragma: no cover
    settings = None  # type: ignore[assignment]


__all__ = [
    "TemplateBoundary",
    "boundary_id_for_page",
    "detect_template_boundaries",
    "detect_template_boundaries_from_sections",
    "extract_template_anchors",
]
