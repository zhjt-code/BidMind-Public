"""
职责: 从已落库检索命中沉淀项目级证据账本
不做: 二次检索、LLM、符合性判断、写入新表
"""

from __future__ import annotations

import asyncio
from typing import Any, Iterable, Mapping, Sequence

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ai.reviewer_parts.evidence_ledger.contracts import (
    EvidenceAtom,
    EvidenceLedger,
    facet_covers_normalized,
    prepare_facet,
)
from app.core.ai.reviewer_parts.evidence_ledger.probe_index import (
    bind_probe_index,
    extend_probe_index,
)
from app.core.retriever.material_key import (
    MATCH_STRONG,
    canonical_material_key,
    match_key_in_compact,
    normalize_material_compact,
)
from app.core.ai.reviewer_parts.evidence_ledger.grading import grade_atom
from app.core.retriever.evidence_coverage import (
    EvidenceFacet,
    build_evidence_facets,
    normalize_text,
)
from app.core.retriever.material_inventory import load_material_facts
from app.core.review_contracts import parse_json_list
from app.models.bid_response_chunk import BidResponseChunk
from app.models.chunk import Chunk


async def build_project_evidence_ledger(
    session: AsyncSession,
    matched_pairs: Sequence[Any],
    facts: Sequence[Any] | None = None,
) -> EvidenceLedger:
    """审查开始前一次构建。无响应或无命中时返回空账本。

    facts 不为 None 则跳过 load_material_facts；为 None 时保持现有加载逻辑。
    为全部 pair（含无 response 的）构造分面并去重，作为 probe_facets 传给 assemble_project_ledger。
    """
    pairs = list(matched_pairs or ())
    if not pairs:
        return EvidenceLedger()

    response_ids: list[int] = []
    facets_by_response: dict[int, tuple[EvidenceFacet, ...]] = {}
    requirement_by_response: dict[int, int] = {}
    probe_seen: dict[tuple[str, str], EvidenceFacet] = {}  # (key, query) → facet，去重

    for pair in pairs:
        response = getattr(pair, "response", None)
        response_id = getattr(response, "id", None) if response is not None else None
        requirement = getattr(pair, "requirement", None)
        req_id = getattr(pair, "requirement_id", None)
        if req_id is None and requirement is not None:
            req_id = getattr(requirement, "id", None)
        description = getattr(requirement, "description", "") if requirement else ""
        subs = parse_json_list(
            getattr(requirement, "sub_requirements", None) if requirement else None
        )
        kws = parse_json_list(
            getattr(requirement, "keywords", None) if requirement else None
        )
        pair_facets = build_evidence_facets(
            description or "",
            [str(item) for item in subs if item is not None],
            keywords=[str(item) for item in kws if item is not None],
        )
        # 全部 pair（含无 response）贡献 probe_facets
        for facet in pair_facets:
            probe_id = (facet.key, facet.query)
            if probe_id not in probe_seen:
                probe_seen[probe_id] = facet
        # 仅有 response 的 pair 参与原子收集
        if response_id is not None and req_id is not None:
            response_ids.append(int(response_id))
            requirement_by_response[int(response_id)] = int(req_id)
            facets_by_response[int(response_id)] = pair_facets

    all_probe_facets = tuple(probe_seen.values())

    if not response_ids and not all_probe_facets:
        return EvidenceLedger()

    # 加载 facts
    if facts is None:
        project_id = None
        for pair in pairs:
            requirement = getattr(pair, "requirement", None)
            project_id = (
                getattr(requirement, "project_id", None) if requirement is not None else None
            )
            if project_id is not None:
                break
        facts_data: Sequence[Any] = ()
        if project_id is not None:
            facts_data = await load_material_facts(session, int(project_id))
    else:
        facts_data = facts

    if not response_ids:
        # 无检索行，但探针分面仍要建索引（无响应条款场景）
        empty = EvidenceLedger()
        return bind_probe_index(empty, all_probe_facets) if all_probe_facets else empty

    stmt = (
        select(
            BidResponseChunk.bid_response_id,
            BidResponseChunk.chunk_id,
            BidResponseChunk.page_num,
            BidResponseChunk.document_id,
            BidResponseChunk.matched_sub_query,
            Chunk.content,
            Chunk.chunk_type,
            Chunk.page_num,
        )
        .outerjoin(Chunk, BidResponseChunk.chunk_id == Chunk.id)
        .where(BidResponseChunk.bid_response_id.in_(response_ids))
    )
    rows = (await session.execute(stmt)).all()
    payload = [
        {
            "bid_response_id": row[0],
            "chunk_id": row[1],
            "page": row[2] if row[2] is not None else row[7],
            "document_id": row[3],
            "matched_sub_query": row[4],
            "content": row[5] or "",
            "chunk_type": row[6] or "",
        }
        for row in rows
    ]
    # ponytail: 匹配在线程中跑，解释器按切换间隔让出事件循环。
    ledger = await asyncio.to_thread(
        assemble_project_ledger,
        payload,
        facets_by_response,
        requirement_by_response,
        facts_data,
        all_probe_facets,
    )
    logger.info("[AI审查] 项目证据账本: atoms={}", len(ledger.atoms))
    return ledger


def assemble_project_ledger(
    payload: Sequence[Mapping[str, Any]],
    facets_by_response: Mapping[int, Sequence[Any]],
    requirement_by_response: Mapping[int, int],
    facts: Sequence[Any],
    probe_facets: Sequence[EvidenceFacet] | None = None,
) -> EvidenceLedger:
    """
    职责: 用已加载的命中行组装项目证据账本
    不做: 数据库读取、LLM、符合性判断

    probe_facets 传入时：collect 不绑索引 → merge → 末尾单次 bind_probe_index。
    未传时：保持旧语义（collect 内部绑 facets_by_response 分面，merge 增量扩展）。
    """
    if probe_facets is not None:
        # 新路径：不绑 → merge → 末尾单次绑
        ledger = collect_atoms_from_rows(
            payload, facets_by_response, requirement_by_response, bind_probes=False
        )
        if facts:
            ledger = merge_inventory_atoms(ledger, facts)
        if probe_facets:
            ledger = bind_probe_index(ledger, probe_facets)
        return ledger
    # 旧路径：collect 内部绑（bind_probes=True），merge 若已有索引则增量扩展
    ledger = collect_atoms_from_rows(
        payload, facets_by_response, requirement_by_response,
    )
    if facts:
        ledger = merge_inventory_atoms(ledger, facts)
    return ledger


def _facet_catalog(
    facets_by_response_id: Mapping[int, Sequence[EvidenceFacet]],
) -> tuple[list[EvidenceFacet], list[EvidenceFacet]]:
    """登记用分面按键去重；查找表保留每个 (键, 查询)。"""
    all_facets: list[EvidenceFacet] = []
    seen_facet_keys: set[str] = set()
    probe_facets: list[EvidenceFacet] = []
    seen_probes: set[tuple[str, str]] = set()
    for facets in facets_by_response_id.values():
        for facet in facets:
            if not facet.key:
                continue
            probe_id = (facet.key, facet.query)
            if probe_id not in seen_probes:
                seen_probes.add(probe_id)
                probe_facets.append(facet)
            if facet.key not in seen_facet_keys:
                seen_facet_keys.add(facet.key)
                all_facets.append(facet)
    return all_facets, probe_facets


def collect_atoms_from_rows(
    rows: Iterable[Mapping[str, Any]],
    facets_by_response_id: Mapping[int, Sequence[EvidenceFacet]],
    requirement_id_by_response_id: Mapping[int, int],
    bind_probes: bool = True,
) -> EvidenceLedger:
    """纯函数: 命中分面的 chunk 登记为原子，同键合并来源要求。

    bind_probes=False 时返回不带索引的账本（由调用方在末尾统一绑定）。
    行循环内 normalize 每行只算一次，分面在循环外预先 prepare_facet。
    """
    all_facets, probe_facets = _facet_catalog(facets_by_response_id)
    # 在循环外预先规范化分面，避免热路径重复 prepare_facet
    prepared_facets = [(prepare_facet(facet), facet.key) for facet in all_facets]

    merged: dict[tuple[str, int | None, int | None], EvidenceAtom] = {}
    for row in rows:
        response_id = row.get("bid_response_id")
        if response_id is None:
            continue
        response_id = int(response_id)
        req_id = requirement_id_by_response_id.get(response_id)
        if req_id is None or not all_facets:
            continue
        content = str(row.get("content") or "")
        if not content.strip():
            continue
        sub_query = str(row.get("matched_sub_query") or "")
        page = row.get("page")
        page_int = int(page) if page is not None else None
        chunk_id = row.get("chunk_id")
        chunk_int = int(chunk_id) if chunk_id is not None else None
        document_id = row.get("document_id")
        doc_int = int(document_id) if document_id is not None else None
        chunk_type = str(row.get("chunk_type") or "")
        excerpt = content.replace("\n", " ").strip()
        if len(excerpt) > 120:
            excerpt = excerpt[:120] + "…"
        # 每行只做一次规范化
        norm_content = normalize_text(content)
        norm_sub = normalize_text(sub_query) if sub_query else ""
        compact = normalize_material_compact(content)

        for pfacet, facet_key in prepared_facets:
            if not (
                facet_covers_normalized(pfacet, norm_content, norm_sub)
                or match_key_in_compact(pfacet.canon_key, compact) == MATCH_STRONG
            ):
                continue
            grade = grade_atom(chunk_type, content, facet_key)
            key = (facet_key, page_int, chunk_int)
            existing = merged.get(key)
            if existing is None:
                merged[key] = EvidenceAtom(
                    kind=facet_key,
                    grade=grade,
                    page=page_int,
                    chunk_id=chunk_int,
                    excerpt=excerpt,
                    source_requirement_ids=(int(req_id),),
                    document_id=doc_int,
                    bid_part=None,
                )
                continue
            sources = tuple(
                dict.fromkeys((*existing.source_requirement_ids, int(req_id)))
            )
            prefer_material = existing.grade == "material" or grade == "material"
            merged[key] = EvidenceAtom(
                kind=existing.kind,
                grade="material" if prefer_material else existing.grade,
                page=existing.page,
                chunk_id=existing.chunk_id,
                excerpt=existing.excerpt,
                source_requirement_ids=sources,
                document_id=existing.document_id or doc_int,
                bid_part=existing.bid_part,
            )
    base = EvidenceLedger(atoms=tuple(merged.values()))
    if bind_probes:
        return bind_probe_index(base, probe_facets)
    return base


def merge_inventory_atoms(ledger: EvidenceLedger, facts: Sequence[Any]) -> EvidenceLedger:
    """库存事实并入账本: 同 (kind,page,chunk) 材料级优先。

    受影响原子 = 新身份 ∪ excerpt 发生变化的身份。
    grade 仅升级且 excerpt 不变的不算受影响，不触发增量重绑。
    有 probe_facets 时用 extend_probe_index 增量扩展，禁止全量 bind_probe_index。
    """
    merged: dict[tuple[str, int | None, int | None], EvidenceAtom] = {}
    for atom in ledger.atoms:
        merged[(atom.kind, atom.page, atom.chunk_id)] = atom

    old_identities: set[tuple[str, int | None, int | None]] = set(merged.keys())
    old_excerpts: dict[tuple[str, int | None, int | None], str] = {
        k: merged[k].excerpt for k in merged
    }

    for fact in facts:
        kind = str(getattr(fact, "canonical_material_key", "") or "").strip()
        if not kind:
            continue
        page = getattr(fact, "physical_page", None)
        page_int = int(page) if page is not None else None
        chunk_id = getattr(fact, "chunk_id", None)
        chunk_int = int(chunk_id) if chunk_id is not None else None
        document_id = getattr(fact, "document_id", None)
        doc_int = int(document_id) if document_id is not None else None
        grade = str(getattr(fact, "grade", "") or "mention")
        excerpt = str(getattr(fact, "excerpt", "") or "")
        key = (kind, page_int, chunk_int)
        bid_part = getattr(fact, "bid_part", None)
        existing = merged.get(key)
        if existing is None:
            merged[key] = EvidenceAtom(
                kind=kind,
                grade=grade,
                page=page_int,
                chunk_id=chunk_int,
                excerpt=excerpt,
                source_requirement_ids=(),
                document_id=doc_int,
                bid_part=str(bid_part) if bid_part else None,
            )
            continue
        prefer_material = existing.grade == "material" or grade == "material"
        merged[key] = EvidenceAtom(
            kind=existing.kind,
            grade="material" if prefer_material else existing.grade,
            page=existing.page,
            chunk_id=existing.chunk_id,
            excerpt=existing.excerpt or excerpt,
            source_requirement_ids=existing.source_requirement_ids,
            document_id=existing.document_id or doc_int,
            bid_part=existing.bid_part or (str(bid_part) if bid_part else None),
        )

    # 受影响原子：新身份 ∪ excerpt 变化的身份
    affected_identities: set[tuple[str, int | None, int | None]] = set()
    for key, atom in merged.items():
        if key not in old_identities:
            affected_identities.add(key)
        elif atom.excerpt != old_excerpts[key]:
            affected_identities.add(key)

    finished_atoms = tuple(merged.values())
    if not ledger.probe_facets:
        return EvidenceLedger(atoms=finished_atoms)

    # 增量扩展：只对受影响原子重评，禁止全量 bind_probe_index
    ledger_to_extend = EvidenceLedger(
        atoms=finished_atoms,
        probe_facets=ledger.probe_facets,
        probe_index=ledger.probe_index,
    )
    affected_atoms = [merged[k] for k in affected_identities]
    return extend_probe_index(ledger_to_extend, affected_atoms)
