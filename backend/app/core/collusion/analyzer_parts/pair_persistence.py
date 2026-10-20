"""串标分析器 S3 持久化 + 算法 hits 收集

Task 8 Step 8：从 CollusionAnalyzer 主类迁出 S3 持久化 6 个方法。
主类保留同名 thin forwarder 兼容外部调用。

迁出范围：
- save_pair_result（63 行 pair 结果落库）
- save_skipped_pairs（48 行跳过对落库）
- query_pair_result（pair 结果 ORM 读）
- get_algorithm_hits（17 行算法命中聚合，含 D4 开关）
- get_algorithm_d1d2_hits（67 行 D1/D2 算法命中收集）

**不迁出**（保留主类）：
- _get_algorithm_d4_hits（test_collusion_p1_fixes.py:80 直接通过
  CollusionAnalyzer._get_algorithm_d4_hits(ext) classmethod 调用，
  迁出会破坏静态契约）

子模块函数签名规范（方案 B）：函数名去前导下划线，self 名字保留。
"""

from __future__ import annotations


# 补全 import 修复
from app.core.collusion.analyzer_parts.findings import _extract_structured_findings
from app.core.collusion.analyzer_parts.dimension_signals import (
    synthesize_dimension_signal_findings,
)
from app.core.collusion.normalizers import is_nearby_phone
from app.core.collusion.normalizers import is_same_address_building
from app.core.database import async_session_factory
from app.models.document import Document

from typing import Optional

from loguru import logger
from sqlalchemy import select

from app.models.collusion_result import CollusionResult
from app.core.collusion.analyzer_parts.algorithmic import (
    build_skipped_pair_baseline,
)
from app.core.collusion.analyzer_parts.findings import _normalize_compare_result
from app.core.collusion.normalizers import (
    is_credit_code_related,
    normalize_cert_code,
    normalize_id_tail,
    normalize_phone,
)
from app.schemas.collusion import is_valid_person_name


def get_algorithm_hits(
    cls,
    extractions: dict[int, dict],
    *,
    include_d4: bool = False,
) -> set[tuple[int, int]]:
    """快速算法检测 D1/D2（默认）/ D4（可选）命中的文件对，用于智能筛对。

    P1-1: include_d4=True 时追加"两份总价比例 0.85–0.99 或等差"的 D4 命中。
    仅当两侧 price_info.total_price 都能解析为数值时才计入。
    """
    hits = cls._get_algorithm_d1d2_hits(extractions)
    if include_d4:
        hits |= cls._get_algorithm_d4_hits(extractions)
    return hits


def get_algorithm_d1d2_hits(extractions: dict[int, dict]) -> set[tuple[int, int]]:
    """快速算法检测D1/D2命中的文件对（用于智能筛对）"""
    from itertools import combinations
    hits: set[tuple[int, int]] = set()
    doc_ids = list(extractions.keys())

    names_by_doc: dict[int, set[str]] = {}
    certs_by_doc: dict[int, set[str]] = {}
    id_tails_by_doc: dict[int, set[str]] = {}
    for doc_id, ext in extractions.items():
        personnel = ext.get("personnel_list") or []
        names = set()
        certs = set()
        id_tails = set()
        for p in personnel:
            if isinstance(p, dict):
                name = (p.get("name") or "").strip()
                if name and len(name) >= 2 and is_valid_person_name(name):
                    names.add(name)
                for cert in p.get("certificate_numbers") or []:
                    cert_norm = normalize_cert_code(cert)
                    if cert_norm:
                        certs.add(cert_norm)
                id_tail = normalize_id_tail(p.get("id_number"))
                if id_tail:
                    id_tails.add(id_tail)
        names_by_doc[doc_id] = names
        certs_by_doc[doc_id] = certs
        id_tails_by_doc[doc_id] = id_tails

    for id_a, id_b in combinations(doc_ids, 2):
        overlap = names_by_doc.get(id_a, set()) & names_by_doc.get(id_b, set())
        cert_overlap = certs_by_doc.get(id_a, set()) & certs_by_doc.get(id_b, set())
        id_overlap = id_tails_by_doc.get(id_a, set()) & id_tails_by_doc.get(id_b, set())
        if overlap or cert_overlap or id_overlap:
            hits.add((min(id_a, id_b), max(id_a, id_b)))
            continue

        ca = extractions[id_a].get("company_info") or {}
        cb = extractions[id_b].get("company_info") or {}
        if isinstance(ca, dict) and isinstance(cb, dict):
            lr_a = (ca.get("legal_representative") or "").strip()
            lr_b = (cb.get("legal_representative") or "").strip()
            if lr_a and lr_b and lr_a == lr_b:
                hits.add((min(id_a, id_b), max(id_a, id_b)))
                continue
            ph_a = (ca.get("contact_phone") or "").strip()
            ph_b = (cb.get("contact_phone") or "").strip()
            if ph_a and ph_b and (
                normalize_phone(ph_a) == normalize_phone(ph_b)
                or is_nearby_phone(ph_a, ph_b)
            ):
                hits.add((min(id_a, id_b), max(id_a, id_b)))
                continue
            if is_credit_code_related(
                ca.get("unified_social_credit_code"),
                cb.get("unified_social_credit_code"),
            ):
                hits.add((min(id_a, id_b), max(id_a, id_b)))
                continue
            if is_same_address_building(
                ca.get("registered_address"),
                cb.get("registered_address"),
            ):
                hits.add((min(id_a, id_b), max(id_a, id_b)))

    return hits


async def save_skipped_pairs(
    self,
    skipped_pairs: list[tuple[int, int]],
    bid_docs: list[Document],
    similarity_result,
    extractions: dict[int, dict] | None = None,
) -> None:
    """为智能跳过的对落库结果。

    C1: 在原"仅落 inconclusive 占位"基础上补跑零成本算法基线
    （D1 人员 / D2 企业 / D4 报价规则 / D8 元数据），命中任一维度时升级为
    ``analysis_mode='algorithm_baseline'``，让一级证据（人员/企业关联）不再
    因智能筛对被丢弃。未命中时维持 inconclusive 占位但标注"已执行算法基线
    比对未命中"，禁止把"未分析"标记为"无风险"。
    """
    if not skipped_pairs:
        return

    doc_map = {d.id: d for d in bid_docs}
    extractions = extractions or {}
    baseline_hits = 0
    baseline_skipped_no_extract = 0

    async with async_session_factory() as save_db:
        for doc_a_id, doc_b_id in skipped_pairs:
            a_id, b_id = min(doc_a_id, doc_b_id), max(doc_a_id, doc_b_id)
            existing = await save_db.execute(
                select(CollusionResult).where(
                    CollusionResult.project_id == self._project_id,
                    CollusionResult.doc_a_id == a_id,
                    CollusionResult.doc_b_id == b_id,
                )
            )
            if existing.scalar_one_or_none():
                continue

            sim_score = similarity_result.pair_scores.get((a_id, b_id), 0.0)
            doc_a = doc_map.get(a_id)
            doc_b = doc_map.get(b_id)
            name_a = doc_a.filename if doc_a else f"文档{a_id}"
            name_b = doc_b.filename if doc_b else f"文档{b_id}"

            info_a = extractions.get(a_id) or {}
            info_b = extractions.get(b_id) or {}

            baseline = None
            if info_a and info_b:
                try:
                    baseline = build_skipped_pair_baseline(
                        info_a, info_b, name_a, name_b,
                    )
                except Exception as err:
                    logger.warning(
                        f"[Analyzer] 跳过对算法基线异常 pair=({a_id},{b_id}): {err}"
                    )
                    baseline = None
            else:
                baseline_skipped_no_extract += 1

            if baseline is not None:
                normalized = _normalize_compare_result(
                    baseline,
                    doc_a_id=a_id,
                    doc_b_id=b_id,
                    doc_a_name=name_a,
                    doc_b_name=name_b,
                )
                overall_risk = normalized.get("overall_risk") or "low"
                key_findings = normalized.get("key_findings") or []
                normalized.setdefault("review_state", "completed")
                normalized.setdefault(
                    "risk_explanation",
                    "智能筛对跳过该对，已执行算法基线比对并命中维度信号，"
                    "建议结合人员/企业关联做人工复核。",
                )
                normalized["analysis_mode"] = "algorithm_baseline"
                save_db.add(CollusionResult(
                    project_id=self._project_id,
                    doc_a_id=a_id,
                    doc_b_id=b_id,
                    overall_risk=overall_risk,
                    similarity_score=sim_score,
                    analysis_mode="algorithm_baseline",
                    findings=key_findings,
                    llm_analysis=normalized,
                ))
                baseline_hits += 1
                continue

            quality_flag = (
                "智能筛对跳过：已执行算法基线比对（D1/D2/D4/D8）未命中，"
                "文本维度未深度分析，禁止据此判定为无风险。"
                if info_a and info_b
                else "智能筛对跳过：因结构化提取缺失，未能执行算法基线比对。"
            )
            save_db.add(CollusionResult(
                project_id=self._project_id,
                doc_a_id=a_id,
                doc_b_id=b_id,
                overall_risk="inconclusive",
                similarity_score=sim_score,
                analysis_mode="skipped",
                findings=[],
                llm_analysis={
                    "overall_risk": "inconclusive",
                    "risk_level": None,
                    "skipped": True,
                    "analysis_state": "skipped",
                    "decision_state": "skipped",
                    "review_state": "unreviewed",
                    "quality_flags": [quality_flag],
                    "risk_explanation": (
                        "该对文件未进入深度分析流程，当前仅能判定为待复核而非无风险。"
                    ),
                },
            ))
        await save_db.commit()

    logger.info(
        f"[Analyzer] 保存{len(skipped_pairs)}对跳过记录 "
        f"(算法基线命中={baseline_hits}, 缺提取={baseline_skipped_no_extract})"
    )


async def query_pair_result(self, doc_a_id: int, doc_b_id: int) -> Optional[CollusionResult]:
    """查询单对 pair 结果记录 (按需短 session, 与长流程主连接解耦): 不再使用 self._db 长持有的连接, 改为独立短 session;
 短 session 自带隔离, 不再需要 _db_lock 串行化保护。
 """
    async with async_session_factory() as session:
        rows = await session.execute(
            select(CollusionResult).where(
                CollusionResult.project_id == self._project_id,
                CollusionResult.doc_a_id == doc_a_id,
                CollusionResult.doc_b_id == doc_b_id,
            )
        )
        return rows.scalar_one_or_none()


def _build_evidence_chunks_payload(
    similar_chunks: Optional[list[dict]],
    *,
    max_pairs: int = 15,
    max_chars: int = 300,
) -> list[dict]:
    """将向量预筛选的高相似 chunk 对压缩为可回显的"重合片段"载荷。

 职责: 把 similarity.py 算出的 similar_chunks（含 text_a/text_b/page/score/
 literal_score/kb_rule_match）裁剪为前端可直接展示的精简结构，并保留
 "是否疑似通用模板"标注（boilerplate_flag / kb_rule_match）。
 不做: 重新计算相似度、判定串标风险、LLM 调用。

 同源教训: 命中白名单时各维度被门控归零、findings 为空，
 用户只看到"重合可能源于通用模板"的结论却看不到具体重合内容，无法甄别。
 本载荷把"到底哪几段重合"显式落库，供 pair 详情按"疑似通用模板"标注展示。
 """
    if not similar_chunks:
        return []
    payload: list[dict] = []
    for pair in similar_chunks[:max_pairs]:
        if not isinstance(pair, dict):
            continue
        text_a = str(pair.get("text_a") or "")
        text_b = str(pair.get("text_b") or "")
        if not text_a.strip() and not text_b.strip():
            continue
        score = pair.get("score")
        literal = pair.get("literal_score")
        payload.append({
            "text_a": text_a[:max_chars],
            "text_b": text_b[:max_chars],
            "page_a": pair.get("page_a"),
            "page_b": pair.get("page_b"),
            "score": round(float(score), 3) if isinstance(score, (int, float)) else None,
            "literal_score": (
                round(float(literal), 3) if isinstance(literal, (int, float)) else None
            ),
            "boilerplate_flag": bool(pair.get("boilerplate_flag")),
            "kb_rule_match": bool(pair.get("kb_rule_match")),
        })
    return payload


async def save_pair_result(
    self,
    doc_a_id: int,
    doc_b_id: int,
    similarity_score: float,
    compare_result: dict,
    analysis_mode: str,
    similar_chunks: Optional[list[dict]] = None,
) -> None:
    """保存文件对对比结果（独立会话+commit，避免干扰主session并发读取）"""
    # 在抽取结构化证据前，注入跨字段串联引擎对当前 pair 的命中
    self._inject_cross_field_findings(compare_result, doc_a_id, doc_b_id)
    await self._backfill_compare_result_evidence_refs(
        compare_result, doc_a_id, doc_b_id,
    )

    def _score(key: str) -> Optional[float]:
        dim = compare_result.get(key)
        return dim.get("score") if isinstance(dim, dict) else None

    structured_findings = _extract_structured_findings(
        compare_result=compare_result,
        doc_a_id=doc_a_id,
        doc_b_id=doc_b_id,
    )
    # T1: 为"有维度得分但无幸存正式证据"的维度补齐参考级信号项，
    # 消除"矩阵/雷达有分但中栏空白"的展示鸿沟（凡得分>0必可见）。
    structured_findings = structured_findings + synthesize_dimension_signal_findings(
        compare_result=compare_result,
        structured_findings=structured_findings,
        doc_a_id=doc_a_id,
        doc_b_id=doc_b_id,
    )

    field_values = {
        "overall_risk": compare_result.get("overall_risk", "inconclusive"),
        "similarity_score": similarity_score,
        "d1_personnel_overlap": _score("d1_personnel"),
        "d2_company_relation": _score("d2_company"),
        "d3_text_similarity": _score("d3_text"),
        "d4_price_pattern": _score("d4_price"),
        "d5_qualification_overlap": _score("d5_qualification"),
        "d6_format_similarity": _score("d6_format"),
        "d7_abnormal_consistency": _score("d7_abnormal"),
        "d8_metadata_similarity": _score("d8_metadata"),
        "findings": structured_findings,
        "evidence_chunks": _build_evidence_chunks_payload(similar_chunks),
        "llm_analysis": compare_result,
        "analysis_mode": analysis_mode,
    }

    async with async_session_factory() as save_db:
        rows = await save_db.execute(
            select(CollusionResult).where(
                CollusionResult.project_id == self._project_id,
                CollusionResult.doc_a_id == doc_a_id,
                CollusionResult.doc_b_id == doc_b_id,
            )
        )
        existing = rows.scalar_one_or_none()
        if existing:
            for k, v in field_values.items():
                setattr(existing, k, v)
        else:
            save_db.add(CollusionResult(
                project_id=self._project_id,
                doc_a_id=doc_a_id,
                doc_b_id=doc_b_id,
                **field_values,
            ))
        await save_db.commit()
