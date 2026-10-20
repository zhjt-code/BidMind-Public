"""FindingPageQuoteVerifyGate — 页码/引文联合校验（B1 重写版本）。

历史教训（a / 已修过 3 次仍复发）：
- 老版本将 page 校验与 quote 校验解耦：page 仅查"页码是否在文档全集"、
 quote 用 SequenceMatcher 在全文档松匹配（ratio>=0.4），并且单边失败仅
 warn 放行。导致 LLM 给出 page=30/quote="钢材配送方案"，但第 30 页根本
 没有该字符，最终仍作为正式证据展示。

B1 新契约（与 collusion.mdc 第六章 6.4 对齐）:
- evidence 需求 (analyzer.`_prefetch_chunks_for_gate` 提供):
 page_sets: dict[doc_id, set[int]] -- 文档已落库的页码集合
 chunk_texts: dict[doc_id, list[(page, content)]] -- 兜底全文匹配
 page_to_contents: dict[doc_id, dict[page, list[content]]]
 -- 按页索引的 chunk 全文
 doc_a_id / doc_b_id

- 校验语义：
 * page 与 quote 都给出 -> 必须存在 chunk 满足
 chunk.page_num == page AND 归一化(quote) ⊂ 归一化(chunk.content)
 否则 verified=False（拒绝放行，转入 attention_items.unverified_quote）
 * 仅 page 没 quote -> page 必须命中 chunk 集合，标 verification_warning="缺少原文引用"
 * 仅 quote 没 page -> 提升匹配阈值到 ratio>=0.7 OR 子串匹配，并尝试回填 page
 * 短 quote (<5 字) 必须能在声称 page 的 chunk 内找到精确子串，不再松匹配全文档

- 单边失败一律 verified=False；删除"page 或 quote 单通过即放行"分支。
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from app.core.ai.gates.base import Gate, GateContext, GateVerdict
# S.2.7: 复用维度 key 真相源，避免 d4_pricing / d4_price 错别字复发
from app.core.collusion.dimension_keys import COLLUSION_DIM_KEYS as _DIM_KEYS
from app.core.collusion.paired_evidence_identity import (
    IDENTITY_DIM_KEYS,
    retract_dim_if_all_unverified,
)

_PAGE_NUM_RE = re.compile(r"第?\s*(\d+)\s*页?")

# 归一化时移除空白与无义符号，便于子串比对（错别字检测除外）
_NORMALIZE_NOISE_RE = re.compile(r"[\s\u3000\u200b\u200c\u200d\ufeff·•・,，。.;；、:：\"'\"\"''()\[\]【】]+")


def _extract_page_num(page_ref: Any) -> int | None:
    if not page_ref:
        return None
    m = _PAGE_NUM_RE.search(str(page_ref))
    return int(m.group(1)) if m else None


def _sanitize_quote(q: Any) -> str | None:
    """与 schemas/collusion.py sanitize_quote 同义的最小清洗。"""
    if q is None:
        return None
    s = str(q).strip()
    return s if s else None


def _normalize_for_substring(text: str) -> str:
    """归一化文本以做子串匹配：去空白与轻量标点，保留可识别字符。"""
    return _NORMALIZE_NOISE_RE.sub("", text or "").lower()


def _quote_in_chunk(quote: str, chunk_content: str, *, allow_fuzzy: bool = True) -> bool:
    """quote 是否在 chunk 中作为子串出现（归一化后比对）。"""
    if not quote or not chunk_content:
        return False
    nq = _normalize_for_substring(quote)
    nc = _normalize_for_substring(chunk_content)
    if not nq:
        return False
    if nq in nc:
        return True
    # D1/D2 身份维度禁止模糊匹配，避免把对侧相似表格行当成同一引文。
    if allow_fuzzy and len(nq) >= 30:
        return SequenceMatcher(None, nq[:200], nc[:1500]).ratio() >= 0.7
    return False


def _verify_page_quote_locality(
    page: int | None,
    quote: str | None,
    doc_id: int,
    *,
    page_sets: dict[int, set[int]],
    chunk_texts: dict[int, list[tuple[int | None, str]]],
    page_to_contents: dict[int, dict[int, list[str]]],
    allow_fuzzy: bool = True,
) -> tuple[bool, str | None, int | None]:
    """联合校验单侧 finding 的 page + quote。

    Returns:
        (verified, reason_if_failed, recovered_page)
        - verified: 是否通过
        - reason_if_failed: verified=False 时的原因
        - recovered_page: 仅 quote 没 page 但匹配成功时回填的 page
    """
    has_page = page is not None
    has_quote = quote is not None and len(quote) >= 1

    # 情形 1：page 与 quote 都没给（finding 不携带本侧定位）
    if not has_page and not has_quote:
        return True, None, None  # 视为本侧未声明定位，不参与校验

    page_in_doc = (
        page in page_sets.get(doc_id, set()) if has_page else False
    )

    # 情形 2：仅 page 没 quote
    if has_page and not has_quote:
        if page_in_doc:
            # 页码合法但缺原文引用，降级为警告（不算失败）
            return True, "缺少原文引用", None
        return False, f"页码 {page} 不在该文档已解析页码集合中", None

    # 情形 3：仅 quote 没 page -> 全文档高阈值匹配，并尝试回填 page
    if has_quote and not has_page:
        normalized_quote = _normalize_for_substring(quote or "")
        if not normalized_quote:
            return False, "原文引用归一化后为空", None
        # 短 quote (<5 字归一化字符) 不再做全文档松匹配
        if len(normalized_quote) < 5:
            return False, "原文引用过短且未提供页码，无法定位", None

        recovered: int | None = None
        for pn, content in chunk_texts.get(doc_id, []):
            if _quote_in_chunk(quote or "", content, allow_fuzzy=allow_fuzzy):
                recovered = pn
                break
        if recovered is not None:
            return True, None, recovered
        return False, "原文引用无法在该文档任一 chunk 中匹配", None

    # 情形 4：page 与 quote 都给出（联合校验，最严）
    assert has_page and has_quote
    if not page_in_doc:
        return False, f"页码 {page} 不在该文档已解析页码集合中", None

    candidates = page_to_contents.get(doc_id, {}).get(page or 0, [])
    if not candidates:
        return False, f"第 {page} 页未抓取到任何 chunk 内容，无法核验引用", None

    # 短 quote 必须在声称 page 的 chunk 内做精确子串匹配（不放行模糊匹配）
    normalized_quote = _normalize_for_substring(quote or "")
    if len(normalized_quote) < 5:
        for content in candidates:
            if normalized_quote and normalized_quote in _normalize_for_substring(content):
                return True, None, None
        return False, f"短引文“{quote}”在声称的第 {page} 页未精确出现", None

    # 长 quote：page 内 chunk 子串/高阈值匹配
    for content in candidates:
        if _quote_in_chunk(quote or "", content, allow_fuzzy=allow_fuzzy):
            return True, None, None
    return False, f"原文引用未出现在声称的第 {page} 页", None


class FindingPageQuoteVerifyGate(Gate):
    """页码/引文联合校验：finding 必须能锚定到具体 chunk 才放行。"""

    gate_name = "FindingPageQuoteVerifyGate"
    flow = "collusion"
    stage = "post_llm"
    order = 50
    default_severity = "enforce"
    description = "页码与引文在 chunk 中的联合锚点验证（B1 重写）"

    def applies(self, ctx: GateContext) -> bool:
        if ctx.flow != "collusion" or ctx.stage != "post_llm":
            return False
        evidence = ctx.evidence or {}
        # B1: 接受新版预取（page_to_contents）；老调用方未提供时，evaluate 内部
        # 会按 chunk_texts 自动构建一次（向后兼容，便于灰度）
        return "page_sets" in evidence and "chunk_texts" in evidence

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        target = ctx.target
        evidence = ctx.evidence or {}
        page_sets: dict[int, set[int]] = evidence["page_sets"]
        chunk_texts: dict[int, list[tuple[int | None, str]]] = evidence["chunk_texts"]
        doc_a_id: int = evidence.get("doc_a_id", 0)
        doc_b_id: int = evidence.get("doc_b_id", 0)

        # B1: 兼容老调用方 — 当 analyzer 未传 page_to_contents 时本地重建
        page_to_contents: dict[int, dict[int, list[str]]] = evidence.get(
            "page_to_contents"
        )
        if page_to_contents is None:
            page_to_contents = {}
            for did, items in chunk_texts.items():
                bucket: dict[int, list[str]] = {}
                for pn, content in items:
                    if pn is None or not content:
                        continue
                    bucket.setdefault(int(pn), []).append(content)
                page_to_contents[did] = bucket

        modified_count = 0
        rejected_count = 0

        for dim_key in _DIM_KEYS:
            dim_data = target.get(dim_key)
            if not isinstance(dim_data, dict):
                continue
            allow_fuzzy = dim_key not in IDENTITY_DIM_KEYS
            for f in dim_data.get("findings") or []:
                if not isinstance(f, dict):
                    continue

                # 已被前置 Gate 拒绝的 finding 不再处理
                if f.get("verified") is False:
                    continue

                pn_a = _extract_page_num(f.get("page_a"))
                pn_b = _extract_page_num(f.get("page_b"))
                quote_a = _sanitize_quote(f.get("quote_a"))
                quote_b = _sanitize_quote(f.get("quote_b"))

                ok_a, reason_a, recovered_a = _verify_page_quote_locality(
                    pn_a, quote_a, doc_a_id,
                    page_sets=page_sets,
                    chunk_texts=chunk_texts,
                    page_to_contents=page_to_contents,
                    allow_fuzzy=allow_fuzzy,
                )
                ok_b, reason_b, recovered_b = _verify_page_quote_locality(
                    pn_b, quote_b, doc_b_id,
                    page_sets=page_sets,
                    chunk_texts=chunk_texts,
                    page_to_contents=page_to_contents,
                    allow_fuzzy=allow_fuzzy,
                )

                # B1: 仅在显式声明本侧定位时记录 verified；未声明侧不影响判定
                if pn_a is not None or quote_a is not None:
                    f["page_a_verified"] = bool(pn_a) and (pn_a in page_sets.get(doc_a_id, set()))
                    f["quote_a_verified"] = bool(quote_a) and ok_a
                if pn_b is not None or quote_b is not None:
                    f["page_b_verified"] = bool(pn_b) and (pn_b in page_sets.get(doc_b_id, set()))
                    f["quote_b_verified"] = bool(quote_b) and ok_b

                # 回填仅有 quote 的 page（提升后续可跳转性）
                if recovered_a is not None and not f.get("page_a"):
                    f["page_a"] = f"第{recovered_a}页"
                    f["page_a_verified"] = True
                    f["quote_a_verified"] = True
                if recovered_b is not None and not f.get("page_b"):
                    f["page_b"] = f"第{recovered_b}页"
                    f["page_b_verified"] = True
                    f["quote_b_verified"] = True

                # B1: 联合校验失败（任一显式声明侧失败）一律拒绝放行
                a_declared = pn_a is not None or quote_a is not None
                b_declared = pn_b is not None or quote_b is not None
                a_failed = a_declared and not ok_a
                b_failed = b_declared and not ok_b

                if a_failed or b_failed:
                    f["verified"] = False
                    reasons = [r for r in (reason_a, reason_b) if r]
                    f.setdefault(
                        "rejection_reason",
                        "页码-原文联合校验失败：" + " / ".join(reasons)
                        if reasons else "页码-原文联合校验失败",
                    )
                    rejected_count += 1
                else:
                    # 软警告：仅 page 没 quote 的情形（reason_a/reason_b 为"缺少原文引用"）
                    soft_reasons = [
                        r for r in (reason_a, reason_b)
                        if r == "缺少原文引用"
                    ]
                    if soft_reasons:
                        f.setdefault("verified", True)
                        f["verification_warning"] = "缺少原文引用，仅页码可核"
                        modified_count += 1
                    else:
                        f.setdefault("verified", True)

                if recovered_a is not None or recovered_b is not None:
                    modified_count += 1

            if retract_dim_if_all_unverified(dim_data):
                rejected_count += 1

        verdict = "enforce_patch" if (modified_count + rejected_count) > 0 else "pass"
        return GateVerdict(
            gate_name=self.gate_name,
            verdict=verdict,
            severity=self.default_severity,
            reason=f"finding_page_quote: rejected={rejected_count} warned={modified_count}",
            confidence=1.0,
            trace={
                "rejected_count": rejected_count,
                "warned_count": modified_count,
            },
        )


# 保留旧 helper 名避免外部 import 失败；新代码请使用 _verify_page_quote_locality
def _verify_quote(
    quote: str | None,
    doc_id: int,
    chunk_texts: dict[int, list[tuple[int | None, str]]],
) -> bool | None:
    """[Deprecated] 兼容老接口；建议迁移到 _verify_page_quote_locality。

    保留语义但收紧阈值（0.4 -> 0.7）以与新版联合校验对齐。
    """
    if not quote or len(quote) < 5:
        return None
    nq = _normalize_for_substring(quote)
    if not nq:
        return None
    best_ratio = 0.0
    for _, chunk_content in chunk_texts.get(doc_id, []):
        nc = _normalize_for_substring(chunk_content)
        if not nc:
            continue
        if nq in nc:
            return True
        ratio = SequenceMatcher(None, nq[:200], nc[:1500]).ratio()
        best_ratio = max(best_ratio, ratio)
        if best_ratio >= 0.7:
            return True
    return best_ratio >= 0.7
