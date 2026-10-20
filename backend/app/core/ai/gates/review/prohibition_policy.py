"""
职责: 从招标要求解析禁止极性（违禁物/允许区/禁区），并判定候选页是否构成禁区泄漏。
不做: 不调用 LLM/DB、不打符合分、不审查 must_have 项是否已提供材料。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Sequence

from app.core.ai.gates.review.scoring_object_detect import detect_quote_forms

DetectKind = Literal["quote", "identity", "literal"]
QuoteLocus = Literal["allowed", "forbidden", "unknown"]

_NEGATION = ("不得", "禁止", "严禁", "不允许", "不可")
_APPEAR_VERBS = ("出现", "包含", "载明", "夹带", "附带", "注明", "填写")
_PRICE_OBJECTS = ("价格", "报价", "单价", "总价", "下浮", "清单价")
_IDENTITY_KEYS = ("名称", "标识", "抬头", "单位名称", "企业名称")
_IDENTITY_MARKERS = ("有限公司", "股份有限", "有限责任公司")
_ACTION_OBJECTS = frozenset({"分包", "转包", "挂靠", "行贿"})
_ALLOWED_HINTS = ("第二信封", "二信封", "开标一览", "报价文件")
_FORBIDDEN_HINTS = ("技术标", "商务标", "投标函", "投标书", "第一信封", "资格文件", "资格标")
_GENERIC_SKIP = frozenset({
    "任何", "相关", "内容", "信息", "事项", "要求", "文件",
    "投标人", "招标人", "投标", "招标",
})
_QUOTE_HINTS = (
    "投标总价", "投标报价", "报价一览", "开标一览", "含税总价", "报价金额",
)
_RULE_COPY_HINTS = ("评标办法", "计算公式", "评分标准", "价格分计算")
_COMPLIANCE_VOICES = ("不", "无", "未", "没有", "绝不", "决不", "杜绝", "避免")
_POSITIVE_VOICES = ("将", "已经", "存在", "包含", "可以", "提供", "进行", "实施", "采用")
_NUMERIC_AMOUNT_RE = re.compile(
    r"(?:人民币|RMB|￥|¥)\s*[\d,，.]+(?:万)?元?"
    r"|[\d,，.]+\s*(?:万元|亿元)"
)
_IN_LOCI_RE = re.compile(r"在([^。；;]{2,40}?)(?:中|内)")
_INTO_LOCI_RE = re.compile(r"(?:放入|置于|装入)([^。；;，,]{2,16})")
_APPEAR_CLAUSE_RE = re.compile(
    r"(?:不得|禁止|严禁|不允许|不可)(?:在[^。]{0,30}(?:中|内))?"
    r"(?:出现|包含|载明|夹带|附带|注明|填写)\s*([^。；;]+)"
)
_ACTION_RE = re.compile(
    r"(?:不得|禁止|严禁|不允许|不可)\s*([\u4e00-\u9fff]{2,8}?)(?=[,，。;；、\s]|$)"
)
_PAREN_RE = re.compile(r"[（(]([^）)]{2,40})[）)]")


@dataclass(frozen=True)
class ProhibitionSpec:
    """
    职责: 一条禁止项的可检索契约。
    不做: 不执行检索、不打分。
    """

    objects: tuple[str, ...]
    allowed_loci: tuple[str, ...]
    forbidden_loci: tuple[str, ...]
    detect_kind: DetectKind

    @property
    def search_terms(self) -> tuple[str, ...]:
        terms: list[str] = []
        if self.detect_kind == "quote":
            terms.extend(("投标函", "投标书"))
            terms.extend(self.forbidden_loci)
        elif self.detect_kind == "identity":
            terms.extend(_IDENTITY_MARKERS)
            terms.extend(self.forbidden_loci)
        else:
            terms.extend(self.objects)
            terms.extend(self.forbidden_loci)
        seen: set[str] = set()
        out: list[str] = []
        for term in terms:
            if not term or term in seen or term in _GENERIC_SKIP:
                continue
            seen.add(term)
            out.append(term)
            if len(out) >= 12:
                break
        return tuple(out)


@dataclass(frozen=True)
class ProhibitionLeakHit:
    """
    职责: 记录一处禁区违禁物命中。
    不做: 不判断整项符合度。
    """

    page: int | None
    section: str
    snippet: str
    locus: QuoteLocus
    obj: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "section": self.section,
            "snippet": self.snippet,
            "locus": self.locus,
            "obj": self.obj,
        }


def parse_prohibition(text: str | None) -> ProhibitionSpec | None:
    """
    职责: 仅从否定词所辖的出现/载明子句或可扫描动作子句生成禁止项契约。
    不做: 不把邻句括号、限价口径、比较性「不得」收成违禁物，不审查须提供项。
    """
    raw = (text or "").strip()
    if not raw or not any(token in raw for token in _NEGATION):
        return None

    objects: list[str] = []
    for match in _APPEAR_CLAUSE_RE.finditer(raw):
        objects.extend(_split_objects(match.group(1)))
    for match in _ACTION_RE.finditer(raw):
        objects.extend(_scannable_action_terms(match.group(1)))
    cleaned = _dedupe(objects)
    if not cleaned:
        return None

    forbidden = _split_loci("、".join(_IN_LOCI_RE.findall(raw)))
    allowed = _split_loci("、".join(_INTO_LOCI_RE.findall(raw)))
    for hint in _ALLOWED_HINTS:
        if hint in raw and hint not in allowed:
            allowed.append(hint)
    for hint in _FORBIDDEN_HINTS:
        if hint in raw and hint not in forbidden and hint not in allowed:
            # 「放入第二信封」已在 allowed；正文仅提及技术标/商务标才进禁区
            if hint in _ALLOWED_HINTS:
                continue
            if hint in ("技术标", "商务标", "投标函", "投标书", "第一信封", "资格文件", "资格标"):
                if hint in raw:
                    forbidden.append(hint)

    return ProhibitionSpec(
        objects=cleaned,
        allowed_loci=tuple(_dedupe(allowed)),
        forbidden_loci=tuple(_dedupe(forbidden)),
        detect_kind=_detect_kind(cleaned),
    )


def is_prohibition(text: str | None) -> bool:
    return parse_prohibition(text) is not None


def prohibition_search_terms(text: str | None) -> tuple[str, ...]:
    spec = parse_prohibition(text)
    return spec.search_terms if spec else ()


def _split_loci(blob: str) -> list[str]:
    return [
        part.strip()
        for part in re.split(r"[、，,和及与以及]", blob or "")
        if 2 <= len(part.strip()) <= 12
    ]


def _split_objects(blob: str) -> list[str]:
    piece = re.split(
        r"必须|否则|放入|置于|不得|禁止|严禁|不允许|不可",
        blob or "",
        maxsplit=1,
    )[0]
    piece = _PAREN_RE.sub(lambda match: f"、{match.group(1)}、", piece)
    out: list[str] = []
    for raw in re.split(r"[、，,和及与以及]", piece):
        token = re.sub(r"^(任何|所有|全部)", "", (raw or "").strip())
        token = re.sub(r"(相关内容|内容|信息|事项|等)$", "", token)
        if 2 <= len(token) <= 12 and token not in _GENERIC_SKIP:
            out.append(token)
        for price in _PRICE_OBJECTS:
            if price in token:
                out.append(price)
    return out


def _scannable_action_terms(action: str) -> list[str]:
    """只收可文件核验的短动作；串通/逾期/以他人名义等行为项不进扫描链。"""
    base = (action or "").strip()
    if not base or any(verb in base for verb in _APPEAR_VERBS):
        return []
    return [obj for obj in _ACTION_OBJECTS if obj in base]


def _dedupe(items: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _detect_kind(objects: Sequence[str]) -> DetectKind:
    if any(
        obj in _PRICE_OBJECTS or "价格" in obj or "报价" in obj
        for obj in objects
    ):
        return "quote"
    if any(any(key in obj for key in _IDENTITY_KEYS) for obj in objects):
        return "identity"
    return "literal"


def has_quote_object(text: str | None) -> bool:
    """金额须带报价提示；下浮率单独成立。避免保证金与声明逗号误伤。"""
    raw = text or ""
    if not raw.strip():
        return False
    if detect_quote_forms(raw).has_rate:
        return True
    if not any(hint in raw for hint in _QUOTE_HINTS):
        return False
    return bool(_NUMERIC_AMOUNT_RE.search(raw))


def classify_locus(
    section_path: str | None,
    content: str | None,
    spec: ProhibitionSpec,
) -> QuoteLocus:
    """允许区优先，避免第二信封被投标函目录词误伤。"""
    blob = f"{section_path or ''}\n{(content or '')[:160]}"
    allowed = list(spec.allowed_loci)
    forbidden = list(spec.forbidden_loci)
    if spec.detect_kind == "quote":
        for hint in ("第二信封", "二信封", "开标一览"):
            if hint not in allowed:
                allowed.append(hint)
        for hint in ("投标函", "投标书"):
            if hint not in forbidden:
                forbidden.append(hint)
    elif spec.detect_kind == "identity":
        for hint in ("技术标", "商务标", "技术方案"):
            if hint not in forbidden:
                forbidden.append(hint)
    if any(hint in blob for hint in allowed):
        return "allowed"
    if any(hint in blob for hint in forbidden):
        return "forbidden"
    return "unknown"


def _window_has(text: str, needle: str, voices: Sequence[str], span: int = 24) -> bool:
    start = 0
    while True:
        idx = text.find(needle, start)
        if idx < 0:
            return False
        window = text[max(0, idx - span): idx + len(needle) + span]
        if any(voice in window for voice in voices):
            return True
        start = idx + len(needle)


def is_rule_echo(content: str, spec: ProhibitionSpec) -> bool:
    """复述禁令本身不是泄漏。"""
    if any(token in content for token in _NEGATION) and any(
        obj in content for obj in spec.objects
    ):
        return True
    return False


def object_present(content: str, spec: ProhibitionSpec) -> bool:
    raw = content or ""
    if not raw.strip():
        return False
    if spec.detect_kind == "quote":
        if any(hint in raw for hint in _RULE_COPY_HINTS):
            return False
        return has_quote_object(raw)
    if spec.detect_kind == "identity":
        if is_rule_echo(raw, spec):
            return False
        if "招标人" in raw[:40] and "投标人" not in raw[:40]:
            return False
        return any(marker in raw for marker in _IDENTITY_MARKERS)
    for obj in spec.objects:
        if obj not in raw:
            continue
        if _window_has(raw, obj, _NEGATION) or _window_has(raw, obj, _COMPLIANCE_VOICES):
            continue
        if obj in _ACTION_OBJECTS or obj[:2] in _ACTION_OBJECTS:
            return _window_has(raw, obj, _POSITIVE_VOICES)
        return True
    return False


def _row_fields(raw: Any) -> tuple[str, str, Any]:
    if isinstance(raw, dict):
        return (
            str(raw.get("content") or ""),
            str(raw.get("section") or raw.get("section_path") or ""),
            raw.get("page", raw.get("page_num")),
        )
    return (
        str(getattr(raw, "content", "") or ""),
        str(
            getattr(raw, "section", None)
            or getattr(raw, "section_path", None)
            or ""
        ),
        getattr(raw, "page", None) if getattr(raw, "page", None) is not None
        else getattr(raw, "page_num", None),
    )


def _snippet(text: str, limit: int = 80) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "…"


def scan_prohibition_leaks(
    rows: Sequence[Any],
    spec: ProhibitionSpec,
    *,
    treat_unknown_as_forbidden: bool = True,
) -> tuple[ProhibitionLeakHit, ...]:
    hits: list[ProhibitionLeakHit] = []
    seen: set[tuple[int | None, str]] = set()
    for raw in rows or ():
        content, section, page = _row_fields(raw)
        if is_rule_echo(content, spec) and spec.detect_kind != "quote":
            continue
        if spec.detect_kind == "quote" and is_rule_echo(content, spec) and not has_quote_object(content):
            continue
        if not object_present(content, spec):
            continue
        locus = classify_locus(section, content, spec)
        if locus == "allowed":
            continue
        # 名称只在点名禁区算泄漏；报价/分包在未知区位仍按禁区处理
        if locus == "unknown" and (
            spec.detect_kind == "identity" or not treat_unknown_as_forbidden
        ):
            continue
        try:
            page_num = int(page) if page is not None else None
        except (TypeError, ValueError):
            page_num = None
        key = (page_num, _snippet(content, 40))
        if key in seen:
            continue
        seen.add(key)
        hits.append(
            ProhibitionLeakHit(
                page=page_num,
                section=section,
                snippet=_snippet(content),
                locus="forbidden" if locus == "unknown" else locus,
                obj=spec.objects[0] if spec.objects else "",
            )
        )
    return tuple(hits)


def hits_from_dicts(items: Iterable[Any]) -> tuple[ProhibitionLeakHit, ...]:
    hits: list[ProhibitionLeakHit] = []
    for item in items or ():
        if isinstance(item, ProhibitionLeakHit):
            hits.append(item)
            continue
        if not isinstance(item, dict):
            continue
        locus = item.get("locus") or "forbidden"
        if locus not in {"allowed", "forbidden", "unknown"}:
            locus = "forbidden"
        page = item.get("page")
        try:
            page_num = int(page) if page is not None else None
        except (TypeError, ValueError):
            page_num = None
        hits.append(
            ProhibitionLeakHit(
                page=page_num,
                section=str(item.get("section") or ""),
                snippet=str(item.get("snippet") or ""),
                locus=locus,
                obj=str(item.get("obj") or ""),
            )
        )
    return tuple(hits)


def format_prohibition_excerpt(hits: Sequence[ProhibitionLeakHit]) -> str:
    if not hits:
        return ""
    lines = [
        "【禁止项反向核验】以下为禁区中检出的违禁内容，不得仅凭合规声明放行："
    ]
    for hit in hits:
        page = f"第{hit.page}页" if hit.page is not None else "页码未知"
        section = hit.section or "禁区"
        lines.append(f"▶ ({page} / {section})\n{hit.snippet}")
    return "\n".join(lines)


def format_prohibition_comment(
    hits: Sequence[ProhibitionLeakHit],
    spec: ProhibitionSpec | None = None,
) -> str:
    pages = "、".join(
        f"第{hit.page}页" for hit in hits if hit.page is not None
    ) or "禁区"
    snippets = "；".join(hit.snippet for hit in hits[:2] if hit.snippet)
    obj = ""
    if spec and spec.objects:
        obj = "、".join(spec.objects[:3])
    elif hits and hits[0].obj:
        obj = hits[0].obj
    label = obj or "违禁内容"
    body = f"招标要求禁止出现{label}。核验发现{pages}载有违禁内容"
    if snippets:
        body += f"（{snippets}）"
    return body + "，不因合规声明或允许区材料而豁免。该项不符合，存在废标风险。"


def prohibition_supplement(pair: Any) -> str:
    req = getattr(pair, "requirement", None)
    text = getattr(req, "description", None) if req is not None else None
    spec = parse_prohibition(text)
    if spec is None:
        return ""
    objects = "、".join(spec.objects[:4]) or "违禁内容"
    allowed = "、".join(spec.allowed_loci[:3]) or "无"
    forbidden = "、".join(spec.forbidden_loci[:4]) or "未点名禁区（默认投标函/技术商务正文）"
    return (
        "\n\n## 【禁止项·反向核验】\n"
        f"本条是禁止项（不得出现：{objects}），不是「找到遵守声明即符合」。\n"
        f"允许区：{allowed}。禁区：{forbidden}。\n"
        "完全符合必须同时成立：允许区隔离成立，且禁区未检出违禁物。"
        "仅有承诺/声明不得判完全符合。"
        "若补充信息出现【禁止项反向核验】命中，必须判不符合并引用该页码。\n"
    )
