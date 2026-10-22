"""
职责: 成对证据的分侧身份——引文指纹、分侧检索词、主体共现、维度分撤回。
不做: LLM 调用、抽取、风险等级推导、前端展示、页码-引文模糊匹配。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

from app.core.collusion.normalizers import normalize_company_name

IDENTITY_DIM_KEYS: tuple[str, ...] = ("d1_personnel", "d2_company")

_FINGERPRINT_NOISE_RE = re.compile(
    r"[\s\u3000·•・,，。.;；、:：\"'\"\"''()\[\]【】]+"
)
_PAGE_NUM_RE = re.compile(r"(\d+)")
_GENERIC_DOC_STEMS = frozenset({
    "document", "file", "bid", "投标", "投标文件", "附件",
})


def quote_fingerprint(quote: object) -> str:
    text = str(quote or "").strip()
    if not text:
        return ""
    return _FINGERPRINT_NOISE_RE.sub("", text).lower()


def quotes_collide(quote_a: object, quote_b: object) -> bool:
    fa = quote_fingerprint(quote_a)
    fb = quote_fingerprint(quote_b)
    return bool(fa and fb and fa == fb)


def keyword_from_quote(quote: object) -> str:
    text = str(quote or "").strip()
    if not text:
        return ""
    keyword = text.split("，")[0].strip()
    return keyword if len(keyword) >= 2 else ""


def doc_id_for_quote_key(
    quote_key: str, doc_a_id: int, doc_b_id: int,
) -> int | None:
    """quote_a 只映射 A 文档，quote_b 只映射 B 文档。"""
    if quote_key == "quote_a":
        return doc_a_id
    if quote_key == "quote_b":
        return doc_b_id
    return None


def iter_side_quote_searches(
    finding: dict,
) -> Iterator[tuple[str, str, str]]:
    """只使用本侧 quote 生成检索词，禁止用 quote_a 去搜 B。"""
    for quote_key, page_key in (("quote_a", "page_a"), ("quote_b", "page_b")):
        keyword = keyword_from_quote(finding.get(quote_key))
        if keyword:
            yield quote_key, page_key, keyword


def side_subject_tokens(
    company_name: object,
    doc_name: object = None,
) -> tuple[str, ...]:
    tokens: list[str] = []
    core = normalize_company_name(str(company_name or ""))
    if len(core) >= 2:
        tokens.append(core)
    stem = Path(str(doc_name or "")).stem.strip()
    if len(stem) >= 2 and stem.lower() not in _GENERIC_DOC_STEMS and stem not in tokens:
        tokens.append(stem)
    return tuple(tokens)


def page_num_from_ref(page_ref: object) -> int | None:
    if page_ref is None:
        return None
    match = _PAGE_NUM_RE.search(str(page_ref))
    return int(match.group(1)) if match else None


def side_has_subject(
    chunks: list[tuple[int | None, str]],
    page_num: int | None,
    tokens: tuple[str, ...],
) -> bool:
    if not tokens or page_num is None:
        return False
    text = "".join(
        content for pn, content in chunks
        if pn == page_num and content
    ).casefold()
    return any(token.casefold() in text for token in tokens)


def retract_dim_if_all_unverified(dim: object) -> bool:
    """维度内全部 finding 均 verified=False 时收回 score/certainty。"""
    if not isinstance(dim, dict):
        return False
    findings = [f for f in (dim.get("findings") or []) if isinstance(f, dict)]
    if not findings:
        return False
    if not all(f.get("verified") is False for f in findings):
        return False
    dim["score"] = 0.0
    dim["certainty"] = 0.0
    dim["gate_rejected"] = True
    return True


def mark_quote_collision(finding: dict, reason: str) -> None:
    finding["verified"] = False
    finding["_quote_collision"] = True
    finding.setdefault("rejection_reason", reason)
