"""
职责: Q8 交叉引用检查——正文段落与表格行里"见第X章/表X/附件X/第X页"的目标是否存在
不做: 不校验编号自身连续性、不做语义引用(V3)、不扫页眉页脚
"""

from __future__ import annotations

import re
from typing import Optional

from app.core.document_qa.checks.numbering_check import _caption_lines
from app.core.document_qa.contracts import (
    CheckerOutcome,
    CheckerStatus,
    DocumentModel,
    Evidence,
    EvidenceItem,
    Finding,
    QaCategory,
    QaCertainty,
    QaSeverity,
)
CODE = "reference"

_MAX_FINDINGS = 15
_CN = "一二三四五六七八九十百"

# 引用句式 -> 目标家族
_REF_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"(?:详见|参见|见)\s*本?(?:文件|标书)?\s*第\s*([{_CN}\d]+(?:\.\d+)*)\s*[章节条]"), "chapter"),
    # 无"第"字变体: "详见3.4节"(要求至少一个点号, 避免"见3节"类短句歧义误报)
    (re.compile(r"(?:详见|参见|见)\s*([0-9]{1,2}(?:\.[0-9]{1,2}){1,3})\s*[节章]"), "chapter"),
    (re.compile(r"(?:详见|参见|见|如)\s*表\s*(\d+(?:[-－–.]\d+)?)"), "table"),
    (re.compile(r"(?:详见|参见|见|如)\s*图\s*(\d+(?:[-－–.]\d+)?)"), "figure"),
    (re.compile(rf"(?:详见|参见|见)\s*附件\s*(\d+|[A-Z]|[{_CN}]+)"), "attachment"),
    (re.compile(rf"(?:详见|参见|见)\s*附录\s*(\d+|[A-Z]|[{_CN}]+)"), "appendix"),
    (re.compile(r"(?:详见|参见|见)\s*第\s*(\d+)\s*页"), "page"),
]

_FAMILY_LABELS = {
    "chapter": "章节", "table": "表", "figure": "图",
    "attachment": "附件", "appendix": "附录", "page": "页",
}


def _cn_to_int(token: str) -> Optional[int]:
    token = token.strip()
    if token.isdigit():
        return int(token)
    # 仅 ASCII 字母走 A=1 分支(CJK 字符 isalpha() 同样为 True, 必须排除)
    if len(token) == 1 and token.isascii() and token.isalpha():
        return ord(token.upper()) - ord("A") + 1
    try:
        from pycnnum import cn2num

        value = cn2num(token)
        return int(value) if value else None
    except Exception:  # noqa: BLE001
        return None


def _normalize_ref(token: str) -> Optional[str]:
    """把引用标识归一为可比较键: 中文数字/字母 -> 阿拉伯; 复合号统一分隔符。"""
    normalized = token.strip().replace("－", "-").replace("–", "-")
    if re.fullmatch(r"\d+(?:[-.]\d+)*", normalized):
        return normalized.replace("-", ".")
    value = _cn_to_int(normalized)
    return str(value) if value is not None else None


def _build_target_index(model: DocumentModel, rules: dict) -> dict[str, set[str]]:
    """目标索引: 家族 -> 已存在编号键集合。"""
    index: dict[str, set[str]] = {
        "chapter": set(), "table": set(), "figure": set(),
        "attachment": set(), "appendix": set(),
    }
    # 章节: 标题树的十进制号与"第N章"序号
    unit_re = re.compile(rf"^第([{_CN}\d]+)[章节部分篇卷]$")
    from app.core.document_qa.author_spans import outline_nodes

    for heading in outline_nodes(model, rules):
        number = (heading.number or "").strip()
        if not number:
            continue
        matched = unit_re.match(number)
        if matched:
            key = _normalize_ref(matched.group(1))
            if key:
                index["chapter"].add(key)
            continue
        if re.fullmatch(r"\d+(?:\.\d+)*", number):
            index["chapter"].add(number)
            # 前缀也可被引用(见第3章 而正文标题是 3.1)
            index["chapter"].add(number.split(".")[0])

    # 图/表/附件/附录: 复用 sequences.yaml 的家族模式扫描说明行
    family_patterns: dict[str, list[re.Pattern[str]]] = {}
    for family in (rules.get("sequences") or {}).get("families", []):
        key = str(family.get("key") or "")
        if key in index:
            family_patterns[key] = [re.compile(p) for p in family.get("patterns", [])]
    for page_num, stripped in _caption_lines(model):
        for key, patterns in family_patterns.items():
            for pattern in patterns:
                matched = pattern.match(stripped + " ")
                if matched:
                    normalized = _normalize_ref(matched.group(1))
                    if normalized:
                        index[key].add(normalized)
                    break
    return index


def _citation_texts(model: DocumentModel) -> list[tuple[int, str]]:
    """引用句来源: 正文跨度(已跨行续接) + 网格单行。单元格黏合不出引用。"""
    from app.core.document_qa.author_spans import grid_line_texts, prose_spans

    texts = [(span.page, span.text) for span in prose_spans(model)]
    texts.extend(grid_line_texts(model))
    return texts


def _target_missing(family: str, token: str, index: dict[str, set[str]], page_count: int) -> bool:
    """引用目标确定不存在才返回 True; 标识无法归一时不判。"""
    if family == "page":
        return not 1 <= int(token) <= page_count
    key = _normalize_ref(token)
    if key is None or key in index[family]:
        return False
    if family == "chapter" and "." not in key:
        # 顶级章引用("见第3章"): 存在该章任一小节即视为可解析;
        # 完整小节号("3.4")必须精确存在, 禁止前缀豁免放行
        return not any(existing.split(".")[0] == key for existing in index["chapter"])
    return True


def check_reference(model: DocumentModel, rules: dict) -> CheckerOutcome:
    index = _build_target_index(model, rules)
    findings: list[Finding] = []
    reported: set[str] = set()

    for page_num, paragraph in _citation_texts(model):
        for pattern, family in _REF_PATTERNS:
            for matched in pattern.finditer(paragraph):
                token = matched.group(1)
                if not _target_missing(family, token, index, model.page_count):
                    continue
                label = _FAMILY_LABELS[family]
                anchor = f"ref:{family}:{token}"
                if anchor in reported:
                    continue
                reported.add(anchor)
                start = max(0, matched.start() - 20)
                excerpt = paragraph[start:matched.end() + 20]
                findings.append(Finding(
                    rule_code="DOC-REF-001",
                    category=QaCategory.REFERENCE,
                    severity=QaSeverity.P2,
                    certainty=QaCertainty.PROBABLE,
                    title="引用目标未找到",
                    message=(
                        f"第{page_num}页引用了{label}「{matched.group(0)}」, "
                        f"但文中未找到对应的{label}{token}"
                    ),
                    suggestion="核对引用编号是否正确, 或被引用内容是否漏排",
                    evidence=Evidence(
                        kind="single",
                        items=(EvidenceItem(excerpt=excerpt, page=page_num),),
                    ),
                    anchor=anchor,
                    closed_measurement=True,
                ))
                if len(findings) >= _MAX_FINDINGS:
                    return CheckerOutcome(code=CODE, status=CheckerStatus.OK, findings=findings)
    return CheckerOutcome(code=CODE, status=CheckerStatus.OK, findings=findings)
