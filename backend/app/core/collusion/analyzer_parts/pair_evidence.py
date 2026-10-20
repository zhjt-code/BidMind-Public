"""串标分析器 S3 证据回填 + CoVe Judge + 跨字段注入

Task 8 Step 8：从 CollusionAnalyzer 主类迁出 S3 证据相关 5 个方法。
主类保留同名 thin forwarder 兼容外部调用。

迁出范围：
- backfill_compare_result_evidence_refs（123 行证据 quote/page 回填）
- enrich_findings_with_context（58 行 finding 上下文丰富）
- inject_cross_field_findings（91 行跨字段命中 -> pair 注入）
- maybe_run_cove_judge_collusion（130 行 CoVe judge 链路）
- prefetch_chunks_for_gate（43 行 Gate 上下文预取）

子模块函数签名规范（方案 B）：函数名去前导下划线，self 名字保留。
"""

from __future__ import annotations


# 补全 import 修复
from app.core.ai.critic.flows import FLOW_COLLUSION

from loguru import logger
from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session_factory
from app.core.collusion.analyzer_parts.constants import _DIM_KEYS, _DIM_MAP
from app.core.collusion.analyzer_parts.findings import (
    _build_evidences_for_pair,
    _compact_evidence_quote,
    _extract_page_number,
    _has_verifiable_evidence_refs,
    _normalize_evidence_text,
)
from app.core.collusion.paired_evidence_identity import (
    doc_id_for_quote_key,
    iter_side_quote_searches,
)
from app.models.chunk import Chunk
from app.schemas.collusion import sanitize_quote


async def enrich_findings_with_context(
    self, compare_result: dict, doc_a_id: int, doc_b_id: int
) -> None:
    """为算法比较的 findings 补充 chunk 原文上下文

    算法比较的 quote 只有姓名和职务等结构化字段，缺乏文档原文语境。
    本方法根据 findings 中的关键词回查 chunk 表，截取包含该关键词的
    原文段落作为 quote，帮助审核人员快速定位文档中的具体位置。
    """
    for dim_key in ("d1_personnel", "d2_company"):
        dim = compare_result.get(dim_key)
        if not isinstance(dim, dict):
            continue
        for finding in dim.get("findings") or []:
            if not isinstance(finding, dict):
                continue
            # 本侧 quote 只检索本侧 document_id，禁止用 quote_a 关键词去搜 B。
            for quote_key, page_key, keyword in iter_side_quote_searches(finding):
                doc_id = doc_id_for_quote_key(quote_key, doc_a_id, doc_b_id)
                if doc_id is None:
                    continue

                # 短 session, 与长流程主连接解耦。
                async with async_session_factory() as session:
                    rows = await session.execute(
                        select(Chunk.content, Chunk.page_num)
                        .where(
                            Chunk.document_id == doc_id,
                            Chunk.content.contains(keyword),
                        )
                        .order_by(Chunk.chunk_index)
                        .limit(1)
                    )
                    chunk = rows.first()

                if not chunk or not chunk.content:
                    continue

                content = chunk.content
                idx = content.find(keyword)
                if idx < 0:
                    continue

                start = max(0, idx - 60)
                end = min(len(content), idx + len(keyword) + 80)
                snippet = content[start:end].strip()
                if start > 0:
                    snippet = "..." + snippet
                if end < len(content):
                    snippet = snippet + "..."

                finding[quote_key] = snippet
                if chunk.page_num and not finding.get(page_key):
                    finding[page_key] = f"第{chunk.page_num}页"


async def backfill_compare_result_evidence_refs(
    self,
    compare_result: dict,
    doc_a_id: int,
    doc_b_id: int,
) -> None:
    """为正式证据补齐页码/原文引用，避免 page-only 或 quote-only 入库。

    只从当前 pair 的原始 chunks 中截取短引用，不生成新的业务判断。
    """
    page_cache: dict[tuple[int, int], str | None] = {}
    chunk_cache: dict[int, list[tuple[int | None, str]]] = {}

    async def _chunks_for_doc(doc_id: int) -> list[tuple[int | None, str]]:
        if doc_id not in chunk_cache:
            # 短 session, 与长流程主连接解耦。
            async with async_session_factory() as session:
                rows = await session.execute(
                    select(Chunk.page_num, Chunk.content)
                    .where(Chunk.document_id == doc_id)
                    .where(Chunk.content.isnot(None))
                    .order_by(Chunk.chunk_index)
                )
                chunk_cache[doc_id] = [
                    (row.page_num, row.content or "") for row in rows.all()
                    if (row.content or "").strip()
                ]
        return chunk_cache[doc_id]

    async def _quote_for_page(doc_id: int, page_ref: object) -> str | None:
        page_num = _extract_page_number(page_ref)
        if page_num is None:
            return None
        cache_key = (doc_id, page_num)
        if cache_key not in page_cache:
            chunks = [
                content for pn, content in await _chunks_for_doc(doc_id)
                if pn == page_num
            ]
            page_cache[cache_key] = _compact_evidence_quote("\n".join(chunks))
        return page_cache[cache_key]

    async def _page_for_quote(doc_id: int, quote: object) -> str | None:
        sanitized = sanitize_quote(quote)
        if not sanitized:
            return None
        needle = _normalize_evidence_text(sanitized)
        if not needle:
            return None
        short_needle = needle[:80]
        for page_num, content in await _chunks_for_doc(doc_id):
            normalized = _normalize_evidence_text(content)
            if needle in normalized or short_needle in normalized:
                return f"第{page_num}页" if page_num is not None else None
        return None

    async def _backfill_side(finding: dict, quote_key: str, page_key: str, doc_id: int) -> None:
        quote = sanitize_quote(finding.get(quote_key))
        page = finding.get(page_key)
        if not quote and page:
            quote = await _quote_for_page(doc_id, page)
            if quote:
                finding[quote_key] = quote
        if quote and not page:
            located_page = await _page_for_quote(doc_id, quote)
            if located_page:
                finding[page_key] = located_page

    async def _backfill_evidence_ref(ev: dict) -> None:
        doc_id = ev.get("doc_id")
        try:
            doc_id_int = int(doc_id) if doc_id is not None else None
        except (TypeError, ValueError):
            doc_id_int = None
        if doc_id_int not in {doc_a_id, doc_b_id}:
            return
        quote = sanitize_quote(ev.get("quote"))
        location = ev.get("location")
        if not quote and location:
            quote = await _quote_for_page(doc_id_int, location)
            if quote:
                ev["quote"] = quote
        if quote and not location:
            located_page = await _page_for_quote(doc_id_int, quote)
            if located_page:
                ev["location"] = located_page

    for dim_key in _DIM_KEYS:
        dim_data = compare_result.get(dim_key)
        if not isinstance(dim_data, dict):
            continue
        for finding in dim_data.get("findings") or []:
            if not isinstance(finding, dict):
                continue

            await _backfill_side(finding, "quote_a", "page_a", doc_a_id)
            await _backfill_side(finding, "quote_b", "page_b", doc_b_id)

            evidences = finding.get("evidences")
            if isinstance(evidences, list):
                for ev in evidences:
                    if isinstance(ev, dict):
                        await _backfill_evidence_ref(ev)

            finding["evidences"] = _build_evidences_for_pair(
                f=finding,
                doc_a_id=doc_a_id,
                doc_b_id=doc_b_id,
                doc_a_name=(compare_result.get("pair_ref") or {}).get("doc_a_name"),
                doc_b_name=(compare_result.get("pair_ref") or {}).get("doc_b_name"),
            )

            if not _has_verifiable_evidence_refs(finding.get("evidences")):
                finding["_missing_evidence_ref"] = True
                finding.setdefault(
                    "rejection_reason",
                    "该 finding 未能从原始文档 chunk 回填可复核页码或原文引用",
                )


async def maybe_run_cove_judge_collusion(
    self,
    compare_result: dict,
    pair_label: str,
    mode: str,
) -> None:
    """B5.10 / B5.12: CoVe + Judge for collusion HIGH risk pairs.

    - CoVe 触发条件: risk_level == "high" (D-021)
    - Judge 抽样: 10% 常规 + 100% 高风险
    - 失败静默, 不影响主流程

    critic-quality-fix：
    - CoVe 从 ``key_findings`` 提取 quote_a/quote_b + page_a/page_b
      作为证据片段传入，而不再仅用 LLM 自身 analysis 文本做“自我验证”。
    - Judge 的 context_summary 由单行升级为结构化摘要（维度得分 + 关键 finding），
      并使用 ``project_id:pair_label`` 作为条目级 sample_key。
    """
    try:
        _risk = (compare_result.get("risk_level") or "").lower()
    except Exception:
        return

    # 统一抽取关键 findings / 命中维度摘要（供 CoVe + Judge 共用）
    key_findings = compare_result.get("key_findings") or []
    evidence_snippets: list[dict] = []
    chunks_for_validation: list[str] = []
    finding_summaries: list[str] = []
    _HIT_DIMS: list[tuple[str, str]] = []
    for _dim_key, (_dim_code, _dim_label) in _DIM_MAP.items():
        _dim_result = compare_result.get(_dim_key)
        if isinstance(_dim_result, dict) and (_dim_result.get("score") or 0) > 0:
            _HIT_DIMS.append((_dim_code, _dim_label))
    for _f in key_findings:
        if not isinstance(_f, dict):
            continue
        _qa = str(_f.get("quote_a") or "").strip()
        _qb = str(_f.get("quote_b") or "").strip()
        _pa = _f.get("page_a")
        _pb = _f.get("page_b")
        if _qa:
            evidence_snippets.append({"page": _pa, "text": f"A: {_qa}"})
            chunks_for_validation.append(_qa)
        if _qb:
            evidence_snippets.append({"page": _pb, "text": f"B: {_qb}"})
            chunks_for_validation.append(_qb)
        _desc = str(_f.get("description") or "").strip()
        if _desc:
            finding_summaries.append(_desc[:160])

    # CoVe: HIGH 触发
    if _risk == "high":
        try:
            from app.core.ai.critic.cove import should_use_cove, run_cove
            if should_use_cove(self._project_id):
                from app.core.ai.ai_factory import get_judge_client
                _client = get_judge_client(
                    project_id=int(self._project_id) if self._project_id else None
                )
                if _client is not None:
                    _dim_desc = (
                        compare_result.get("risk_explanation")
                        or compare_result.get("overall_risk", "")
                    )
                    _page_a_label = "N/A"
                    _page_b_label = "N/A"
                    for _f in key_findings:
                        if isinstance(_f, dict):
                            if _page_a_label == "N/A" and _f.get("page_a"):
                                _page_a_label = str(_f.get("page_a"))
                            if _page_b_label == "N/A" and _f.get("page_b"):
                                _page_b_label = str(_f.get("page_b"))
                            if _page_a_label != "N/A" and _page_b_label != "N/A":
                                break
                    _result = await run_cove(
                        trigger="collusion_high",
                        template_vars={
                            "page_a": _page_a_label,
                            "page_b": _page_b_label,
                            "dimension": str(_dim_desc)[:200],
                        },
                        chunks=chunks_for_validation,
                        llm_client=_client,
                        flow=FLOW_COLLUSION,
                        evidence_snippets=evidence_snippets or None,
                    )
                    if _result.should_escalate and settings.FAILURE_STATE_ENABLED:
                        compare_result["failure_state"] = "needs_review"
                        _tag = _result.outcome
                        if _result.reason:
                            _tag = f"{_tag}/{_result.reason}"
                        compare_result["failure_reason"] = (
                            f"CoVe disagree: {_tag}"
                        )
        except Exception as e:
            logger.debug(f"CoVe collusion degraded (pair={pair_label}): {e}")

    # Judge: 10% + 100% high
    try:
        from app.core.ai.critic.judge_sampler import maybe_run_judge
        context_lines = [
            f"串标对: {pair_label} (mode={mode})",
            f"命中维度: {', '.join(code for code, _ in _HIT_DIMS) or '无'}",
            f"综合风险: {compare_result.get('overall_risk', 'inconclusive')}",
            f"单维风险: {compare_result.get('risk_level') or 'N/A'}",
        ]
        _rexp = compare_result.get("risk_explanation")
        if _rexp:
            context_lines.append(f"主判理由: {str(_rexp)[:400]}")
        if finding_summaries:
            context_lines.append(
                "关键 findings: " + " | ".join(finding_summaries[:5])
            )

        await maybe_run_judge(
            project_id=self._project_id,
            original_output={
                "risk_level": compare_result.get("risk_level"),
                "overall_risk": compare_result.get("overall_risk"),
                "analysis_state": compare_result.get("analysis_state"),
                "hit_dims": [code for code, _ in _HIT_DIMS],
            },
            context_summary="\n".join(context_lines),
            flow=FLOW_COLLUSION,
            is_high_risk=(_risk == "high"),
            sample_key=f"{self._project_id}:{pair_label}",
        )
    except Exception as e:
        logger.debug(f"Judge collusion degraded (pair={pair_label}): {e}")


async def prefetch_chunks_for_gate(
    self,
    doc_a_id: int,
    doc_b_id: int,
) -> tuple[
    dict[int, set[int]],
    dict[int, list[tuple[int | None, str]]],
]:
    """Pre-fetch chunk locality structures for FindingPageQuoteVerifyGate.

    B2: 在原 page_sets / chunk_texts 之外，构建 page -> contents 索引并
    通过 self._page_to_contents_cache 暴露给 Gate（通过 evidence dict 注入）。
    chunk_texts 截断长度提升到 1500 字符，以保证 1-2 段长引文能落入 chunk。
    """
    page_sets: dict[int, set[int]] = {doc_a_id: set(), doc_b_id: set()}
    chunk_texts: dict[int, list[tuple[int | None, str]]] = {
        doc_a_id: [], doc_b_id: [],
    }
    page_to_contents: dict[int, dict[int, list[str]]] = {
        doc_a_id: {}, doc_b_id: {},
    }
    # 短 session, 与长流程主连接解耦; 短 session 自带隔离, 不再需要 _db_lock。
    async with async_session_factory() as session:
        for did in (doc_a_id, doc_b_id):
            rows = await session.execute(
                select(Chunk.page_num, Chunk.content)
                .where(Chunk.document_id == did)
                .where(Chunk.content.isnot(None))
                .where(Chunk.content != "")
            )
            for pn, content in rows.fetchall():
                if pn is not None:
                    page_sets[did].add(int(pn))
                if content:
                    # B1/B2: 提升截断长度，避免长引文落不到 chunk 内
                    truncated = content[:1500]
                    chunk_texts[did].append((pn, truncated))
                    if pn is not None:
                        page_to_contents[did].setdefault(int(pn), []).append(truncated)
    # 暴露给本对的 Gate evidence（在调用 build_evidence 处合并）
    self._last_page_to_contents = page_to_contents
    return page_sets, chunk_texts


def inject_cross_field_findings(
    self,
    compare_result: dict,
    doc_a_id: int,
    doc_b_id: int,
) -> None:
    """将跨字段串联引擎的命中注入到 compare_result 的对应维度。

    - 提升对应维度 score / certainty（确保 _extract_structured_findings 不会过滤掉）
    - 追加 finding 到该维度的 findings 列表（携带 risk_pattern 等扩展字段）
    - 矫正 evidence_level（first 不可被 second 覆盖；second 不可被 third 覆盖）
    """
    gf = getattr(self, "_global_features_obj", None)
    if gf is None:
        return
    hits = getattr(gf, "cross_field_hits", None) or []
    if not hits:
        return

    target_pair_id = f"{min(doc_a_id, doc_b_id)}:{max(doc_a_id, doc_b_id)}"

    # 维度代号 -> compare_result key 的映射
    dim_code_to_key = {code: key for key, (code, _) in _DIM_MAP.items()}
    level_priority = {"first": 0, "second": 1, "third": 2}

    for h in hits:
        if getattr(h, "pair_id", None) != target_pair_id:
            continue
        target_dim_code = (h.target_dim or "").upper()
        dim_key = dim_code_to_key.get(target_dim_code)
        if not dim_key:
            continue

        dim_data = compare_result.setdefault(dim_key, {})
        if not isinstance(dim_data, dict):
            continue

        # 提升 score / certainty（不能让 _extract_structured_findings 因为 score=0 过滤掉）
        cert = float(getattr(h, "certainty", 0.7) or 0.7)
        old_score = float(dim_data.get("score") or 0)
        dim_data["score"] = max(old_score, cert)
        old_cert = float(dim_data.get("certainty") or 0)
        dim_data["certainty"] = max(old_cert, cert)

        # 矫正 evidence_level
        cur_level = dim_data.get("evidence_level") or "third"
        new_level = h.level or cur_level
        if level_priority.get(new_level, 9) < level_priority.get(cur_level, 9):
            dim_data["evidence_level"] = new_level

        # 构造 finding 并追加（与 LLM 输出 finding 保持兼容）
        findings_list = dim_data.setdefault("findings", [])
        if not isinstance(findings_list, list):
            continue

        # 决定 quote_a/page_a 是文档 doc_a_id 一侧
        if h.left_doc_id == doc_a_id:
            quote_a, page_a = h.left_quote, h.left_page
            quote_b, page_b = h.right_quote, h.right_page
            source_field_a, source_field_b = h.left_field, h.right_field
        else:
            quote_a, page_a = h.right_quote, h.right_page
            quote_b, page_b = h.left_quote, h.left_page
            source_field_a, source_field_b = h.right_field, h.left_field

        findings_list.append({
            "summary": h.description,
            "detail": (
                f"跨字段串联命中: 规则 {h.rule_id} ({h.risk_pattern})。"
                + (f" 法规对照: {h.legal_reference}" if h.legal_reference else "")
            ),
            "quote_a": quote_a,
            "page_a": page_a,
            "quote_b": quote_b,
            "page_b": page_b,
            "similarity": None,
            "verified": True,
            "_cross_field": True,
            "risk_pattern": h.risk_pattern,
            "source_field_a": source_field_a,
            "source_field_b": source_field_b,
            "legal_reference": h.legal_reference,
        })

        # 同步标记 quality_flags 让 UI 可识别
        qf = compare_result.setdefault("quality_flags", [])
        if isinstance(qf, list):
            tag = f"cross_field:{h.risk_pattern}"
            if tag not in qf:
                qf.append(tag)
