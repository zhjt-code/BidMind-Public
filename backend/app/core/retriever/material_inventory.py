"""
职责: 按名词全集扫描信封证据源产出/查询 MaterialFact，提供单调并集持久化
不做: 符合性判断、LLM、终态裁决
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Optional, Sequence

from loguru import logger
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ai.reviewer_parts.evidence_ledger.grading import grade_atom
from app.core.document_qa.normalizers import normalize_slot_value
from app.core.parser.toc_parser import TocParser, deserialize_toc_map
from app.core.retriever.evidence_coverage import build_evidence_facets
from app.core.retriever.evidence_pipeline.entity_retriever import _ID_PATTERNS
from app.core.retriever.material_key import (
    MATCH_STRONG,
    canonical_material_key,
    match_key_in_compact,
    match_material_in_text,
    normalize_material_compact,
)
from app.core.sql_bind_budget import (
    extra_binds_beyond_values,
    iter_bind_safe_row_batches,
)
from app.core.sql_column_fit import fit_string_column
from app.core.retriever.query_decomposer import _extract_basis_objects, _extract_objects
from app.core.review_contracts import parse_json_list
from app.models.bid_response import BidResponse
from app.models.bid_response_chunk import BidResponseChunk
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.material_fact import MaterialFact as MaterialFactRow
from app.models.requirement import Requirement

SOURCE_LEXICAL = "lexical"
SOURCE_TOC = "toc"
SOURCE_OCR = "ocr"
SOURCE_RETRIEVAL_BAG = "retrieval_bag"
SOURCE_REVIEW_SEARCH = "review_search"

GRADE_RANK = {"mention": 1, "material": 2}
_EXCERPT_LIMIT = 120
_ORG_RE = re.compile(
    r"([\u4e00-\u9fff]{2,30}(?:股份有限公司|有限责任公司|集团有限公司|有限公司|集团公司))"
)
_OCR_CHUNK_TYPES = ("image_evidence", "image_annotation")


@dataclass(frozen=True)
class MaterialFact:
    canonical_material_key: str
    raw_noun: str
    grade: str
    document_id: int
    bid_part: Optional[str]
    chunk_id: int
    physical_page: int
    identifier: Optional[str]
    subject: Optional[str]
    source_channel: str
    excerpt: str


def collect_noun_universe(requirements: Sequence[Any]) -> tuple[str, ...]:
    """名词全集 = keywords ∪ 分面键 ∪ 依据/提供句式客体。"""
    seen: list[str] = []
    seen_set: set[str] = set()

    def add(raw: str) -> None:
        key = canonical_material_key(raw)
        if len(key) < 2 or key in seen_set:
            return
        seen_set.add(key)
        seen.append(key)

    for requirement in requirements or ():
        description = str(getattr(requirement, "description", "") or "")
        keywords = parse_json_list(getattr(requirement, "keywords", None))
        subs = parse_json_list(getattr(requirement, "sub_requirements", None))
        keyword_texts = [str(item) for item in keywords if item]
        for keyword in keyword_texts:
            add(keyword)
        facets = build_evidence_facets(
            description,
            [str(item) for item in subs if item],
            keywords=keyword_texts,
        )
        for facet in facets:
            add(facet.key)
        for noun in _extract_objects(description):
            add(noun)
        for noun in _extract_basis_objects(description):
            add(noun)
    return tuple(seen)


def fact_identity(fact: MaterialFact) -> tuple[str, int, int, int]:
    return (
        fact.canonical_material_key,
        fact.document_id,
        fact.chunk_id,
        fact.physical_page,
    )


def merge_fact_union(facts: Iterable[Optional[MaterialFact]]) -> tuple[MaterialFact, ...]:
    """单调并集: 同身份只保留一行, grade 冲突取高, 证号/主体只增不减。"""
    merged: dict[tuple[str, int, int, int], MaterialFact] = {}
    for fact in facts:
        if fact is None:
            continue
        key = fact_identity(fact)
        existing = merged.get(key)
        merged[key] = fact if existing is None else _prefer_fact(existing, fact)
    return tuple(merged.values())


def facts_from_chunk(
    *,
    noun: str,
    chunk_id: int,
    document_id: int,
    bid_part: Optional[str],
    physical_page: Optional[int],
    content: str,
    chunk_type: str | None,
    source_channel: str,
    section_path: str | None = None,
    match_already_strong: bool = False,
) -> Optional[MaterialFact]:
    haystack = " ".join(part for part in (content, section_path) if part)
    if (
        not match_already_strong
        and match_material_in_text(noun, haystack) != MATCH_STRONG
    ):
        return None
    key = canonical_material_key(noun)
    page = int(physical_page) if physical_page is not None else 0
    return MaterialFact(
        canonical_material_key=key,
        raw_noun=noun,
        grade=grade_atom(chunk_type, content or "", key or noun),
        document_id=int(document_id),
        bid_part=bid_part,
        chunk_id=int(chunk_id),
        physical_page=page,
        identifier=_extract_identifier(content or ""),
        subject=_extract_subject(content or ""),
        source_channel=source_channel,
        excerpt=_excerpt(content or ""),
    )


@dataclass(frozen=True, eq=False)
class MaterialInventory:
    """
    职责: 一次建组多次强匹配查询的项目级材料库存快照
    不做: DB 读写、匹配规则、分级、符合性判断
    """

    facts: tuple[MaterialFact, ...]
    _groups: dict = field(default_factory=dict, repr=False, compare=False)
    _memo: dict = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_facts(cls, facts: Iterable[MaterialFact]) -> MaterialInventory:
        """按 (canonical_compact, raw_compact) 去重分组; 每组存升序位置元组。"""
        facts_tuple = tuple(facts)
        instance = cls(facts=facts_tuple)
        groups: dict[tuple[str, str], list[int]] = {}
        for idx, fact in enumerate(facts_tuple):
            key_compact = normalize_material_compact(fact.canonical_material_key)
            raw_compact = normalize_material_compact(fact.raw_noun)
            group_key = (key_compact, raw_compact)
            if group_key not in groups:
                groups[group_key] = []
            groups[group_key].append(idx)
        for gk, pos_list in groups.items():
            instance._groups[gk] = tuple(sorted(pos_list))
        return instance

    def __len__(self) -> int:
        return len(self.facts)

    def __iter__(self):
        return iter(self.facts)

    def __bool__(self) -> bool:
        return bool(self.facts)

    def strong_hits(
        self, noun: str, material_grade_only: bool = True
    ) -> tuple[MaterialFact, ...]:
        """canonical_material_key(noun) 只算一次; 结果按位置序返回。"""
        canon = canonical_material_key(noun)
        if not canon or len(canon) < 2:
            return ()
        memo_key = (canon, material_grade_only)
        cached = self._memo.get(memo_key)
        if cached is not None:
            return cached
        matching_positions: list[int] = []
        for (key_compact, raw_compact), positions in self._groups.items():
            if (
                match_key_in_compact(canon, key_compact) == MATCH_STRONG
                or match_key_in_compact(canon, raw_compact) == MATCH_STRONG
            ):
                matching_positions.extend(positions)
        matching_positions.sort()
        result: tuple[MaterialFact, ...] = tuple(
            self.facts[i]
            for i in matching_positions
            if not material_grade_only or self.facts[i].grade == "material"
        )
        self._memo[memo_key] = result
        return result

    def with_facts(self, new: Iterable[MaterialFact]) -> MaterialInventory:
        """先存在者优先; 与 merge_fact_union 语义一致。"""
        return MaterialInventory.from_facts(merge_fact_union((*self.facts, *new)))


def inventory_strong_hits(
    facts: Sequence[MaterialFact],
    noun: str,
    *,
    material_grade_only: bool = True,
) -> tuple[MaterialFact, ...]:
    """库存强匹配命中; 默认只返回材料级, 提及级不硬定谳。"""
    if isinstance(facts, MaterialInventory):
        return facts.strong_hits(noun, material_grade_only)
    hits: list[MaterialFact] = []
    for fact in facts:
        if match_material_in_text(noun, fact.canonical_material_key) != MATCH_STRONG:
            if match_material_in_text(noun, fact.raw_noun) != MATCH_STRONG:
                continue
        if material_grade_only and fact.grade != "material":
            continue
        hits.append(fact)
    return tuple(hits)


async def build_and_persist_project_inventory(
    session: AsyncSession,
    project_id: int,
    documents: Sequence[Document],
    requirements: Sequence[Any],
) -> tuple[MaterialFact, ...]:
    """信封加载后扫描文档级库存并幂等落库。"""
    nouns = collect_noun_universe(requirements)
    document_ids = tuple(int(doc.id) for doc in documents if getattr(doc, "id", None))
    if not nouns or not document_ids:
        return ()
    facts = await scan_envelope_material_facts(
        session, documents, nouns
    )
    await persist_material_facts(session, project_id, facts)
    logger.info(
        "[材料库存] project_id={} nouns={} facts={}",
        project_id,
        len(nouns),
        len(facts),
    )
    return facts


async def scan_envelope_material_facts(
    session: AsyncSession,
    documents: Sequence[Document],
    nouns: Sequence[str],
) -> tuple[MaterialFact, ...]:
    """词法全匹配 ∪ v2 目录线索 ∪ OCR chunk, 不受 top_k 帽。"""
    document_by_id = {
        int(doc.id): doc for doc in documents if getattr(doc, "id", None) is not None
    }
    if not document_by_id or not nouns:
        return ()
    chunks = await _load_candidate_chunks(session, tuple(document_by_id), nouns)
    collected: list[MaterialFact] = []
    toc_pages_by_doc = {
        doc_id: _enabled_toc_pages(document_by_id[doc_id], nouns)
        for doc_id in document_by_id
    }
    for chunk in chunks:
        document = document_by_id.get(int(chunk.document_id))
        if document is None:
            continue
        bid_part = getattr(document, "bid_part", None)
        page = chunk.page_num
        chunk_type = chunk.chunk_type or "text"
        for noun in nouns:
            channel = _channel_for_chunk(
                chunk_type,
                page,
                toc_pages_by_doc.get(int(chunk.document_id), frozenset()),
            )
            fact = facts_from_chunk(
                noun=noun,
                chunk_id=int(chunk.id),
                document_id=int(chunk.document_id),
                bid_part=bid_part,
                physical_page=page,
                content=chunk.content or "",
                chunk_type=chunk_type,
                source_channel=channel,
                section_path=chunk.section_path,
            )
            if fact is not None:
                collected.append(fact)
    return merge_fact_union(collected)


async def persist_material_facts(
    session: AsyncSession,
    project_id: int,
    facts: Sequence[MaterialFact],
) -> int:
    """ON CONFLICT 单调并集写入, 重启重入安全。"""
    rows = [
        _fit_material_fact_persist_row(
            {
                "project_id": project_id,
                "canonical_material_key": fact.canonical_material_key,
                "raw_noun": fact.raw_noun,
                "grade": fact.grade,
                "document_id": fact.document_id,
                "bid_part": fact.bid_part,
                "chunk_id": fact.chunk_id,
                "physical_page": fact.physical_page,
                "identifier": fact.identifier,
                "subject": fact.subject,
                "source_channel": fact.source_channel,
                "excerpt": fact.excerpt,
            }
        )
        for fact in facts
        if fact is not None
    ]
    if not rows:
        return 0
    probe = _material_fact_upsert_stmt(rows[:1])
    extra = extra_binds_beyond_values(
        probe,
        row_count=1,
        column_count=len(rows[0]),
    )
    written = 0
    for batch in iter_bind_safe_row_batches(rows, extra_binds=extra):
        result = await session.execute(_material_fact_upsert_stmt(batch))
        written += int(result.rowcount or 0)
    return written


def _fit_material_fact_persist_row(row: dict[str, Any]) -> dict[str, Any]:
    """按 material_facts 列宽裁剪字符串字段, 宽度取自模型列定义。"""
    columns = MaterialFactRow.__table__.c
    fitted: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, str):
            fitted[key] = fit_string_column(value, columns[key])
        else:
            fitted[key] = value
    return fitted


def _material_fact_upsert_stmt(rows: Sequence[dict[str, Any]]):
    """构造材料库存单批 INSERT ... ON CONFLICT 单调并集语句。"""
    stmt = pg_insert(MaterialFactRow).values(rows)
    excluded = stmt.excluded
    return stmt.on_conflict_do_update(
        constraint="uq_material_facts_identity",
        set_={
            "grade": _sql_higher_grade(MaterialFactRow.grade, excluded.grade),
            "identifier": _sql_fill(MaterialFactRow.identifier, excluded.identifier),
            "subject": _sql_fill(MaterialFactRow.subject, excluded.subject),
            "excerpt": _sql_keep_material_excerpt(
                MaterialFactRow.grade,
                MaterialFactRow.excerpt,
                excluded.grade,
                excluded.excerpt,
            ),
            "source_channel": _sql_keep_channel(
                MaterialFactRow.grade,
                MaterialFactRow.source_channel,
                excluded.grade,
                excluded.source_channel,
            ),
            "raw_noun": MaterialFactRow.raw_noun,
        },
    )


async def load_material_facts(
    session: AsyncSession,
    project_id: int,
) -> MaterialInventory:
    t0 = time.monotonic()
    stmt = select(MaterialFactRow).where(MaterialFactRow.project_id == project_id)
    rows = list((await session.execute(stmt)).scalars().all())
    facts = tuple(_row_to_fact(row) for row in rows)
    inventory = MaterialInventory.from_facts(facts)
    ms = (time.monotonic() - t0) * 1000
    logger.info(
        "材料库存索引: facts={} groups={} ms={:.1f}",
        len(facts),
        len(inventory._groups),
        ms,
    )
    return inventory


def scan_hits_into_facts(
    prepared: Sequence[tuple[Any, ...]],
    nouns: Sequence[str],
    source_channel: str,
) -> list[MaterialFact]:
    """
    职责: 用既有强匹配从命中行收集材料事实
    不做: 持久化、改写匹配规则、符合性判断
    """
    keyed = [
        (noun, canonical_material_key(noun))
        for noun in nouns
        if noun
    ]
    collected: list[MaterialFact] = []
    for item in prepared:
        chunk_id, document_id, content, chunk_type, page, bid_part = item
        compact = normalize_material_compact(content or "")
        for noun, key in keyed:
            if match_key_in_compact(key, compact) != MATCH_STRONG:
                continue
            fact = facts_from_chunk(
                noun=noun,
                chunk_id=int(chunk_id),
                document_id=int(document_id),
                bid_part=bid_part,
                physical_page=page,
                content=content or "",
                chunk_type=chunk_type,
                source_channel=source_channel,
                match_already_strong=True,
            )
            if fact is not None:
                collected.append(fact)
    return collected


async def merge_search_hits_into_inventory(
    session: AsyncSession,
    project_id: int,
    hits: Sequence[Any],
    nouns: Sequence[str],
    *,
    bid_part_by_document: Optional[dict[int, Optional[str]]] = None,
    source_channel: str = SOURCE_RETRIEVAL_BAG,
) -> tuple[MaterialFact, ...]:
    """提取完成/审查补搜命中回流库存。"""
    parts = bid_part_by_document or {}
    prepared: list[tuple[Any, ...]] = []
    for hit in hits or ():
        chunk_id = getattr(hit, "chunk_id", None)
        document_id = getattr(hit, "document_id", None)
        if chunk_id is None or document_id is None:
            continue
        document_id = int(document_id)
        prepared.append((
            int(chunk_id),
            document_id,
            getattr(hit, "content", "") or "",
            getattr(hit, "chunk_type", None) or "text",
            getattr(hit, "page_num", None),
            parts.get(document_id),
        ))
    # ponytail: 匹配在线程中跑，解释器按切换间隔让出事件循环。
    # 上限: 单次底层字符串扫描仍占 GIL；心跳若再被拖过秒级，改为进程池。
    collected = await asyncio.to_thread(
        scan_hits_into_facts,
        prepared,
        tuple(nouns),
        source_channel,
    )
    merged = merge_fact_union(collected)
    await persist_material_facts(session, project_id, merged)
    return merged


async def merge_project_retrieval_bags(
    session: AsyncSession,
    project_id: int,
    nouns: Sequence[str],
    *,
    bid_part_by_document: Optional[dict[int, Optional[str]]] = None,
) -> tuple[MaterialFact, ...]:
    """提取完成后把全部 BidResponseChunk 命中并入库存。"""
    stmt = (
        select(
            BidResponseChunk.chunk_id,
            BidResponseChunk.document_id,
            BidResponseChunk.page_num,
            Chunk.content,
            Chunk.chunk_type,
        )
        .outerjoin(Chunk, BidResponseChunk.chunk_id == Chunk.id)
        .join(BidResponse, BidResponse.id == BidResponseChunk.bid_response_id)
        .join(Requirement, Requirement.id == BidResponse.requirement_id)
        .where(
            Requirement.project_id == project_id,
            BidResponseChunk.chunk_id.isnot(None),
        )
    )
    rows = (await session.execute(stmt)).all()

    class _Hit:
        __slots__ = ("chunk_id", "document_id", "content", "page_num", "chunk_type")

        def __init__(self, chunk_id, document_id, content, page_num, chunk_type):
            self.chunk_id = chunk_id
            self.document_id = document_id
            self.content = content or ""
            self.page_num = page_num
            self.chunk_type = chunk_type or "text"

    hits = [
        _Hit(row[0], row[1], row[3], row[2], row[4])
        for row in rows
        if row[0] is not None
    ]
    return await merge_search_hits_into_inventory(
        session,
        project_id,
        hits,
        nouns,
        bid_part_by_document=bid_part_by_document,
        source_channel=SOURCE_RETRIEVAL_BAG,
    )


def _prefer_fact(existing: MaterialFact, incoming: MaterialFact) -> MaterialFact:
    existing_rank = GRADE_RANK.get(existing.grade, 0)
    incoming_rank = GRADE_RANK.get(incoming.grade, 0)
    if incoming_rank > existing_rank:
        winner, loser = incoming, existing
    else:
        winner, loser = existing, incoming
    return replace(
        winner,
        identifier=winner.identifier or loser.identifier,
        subject=winner.subject or loser.subject,
    )


def _channel_for_chunk(
    chunk_type: str,
    page: Optional[int],
    toc_pages: frozenset[int],
) -> str:
    if (chunk_type or "") in _OCR_CHUNK_TYPES:
        return SOURCE_OCR
    if page is not None and int(page) in toc_pages:
        return SOURCE_TOC
    return SOURCE_LEXICAL


def _enabled_toc_pages(document: Document, nouns: Sequence[str]) -> frozenset[int]:
    toc_map = deserialize_toc_map(getattr(document, "toc_map", None))
    if not toc_map or toc_map.get("version") != 2 or not toc_map.get("enabled"):
        return frozenset()
    parser = TocParser()
    pages: set[int] = set()
    for noun in nouns:
        pages.update(parser.lookup_physical_pages(noun, toc_map))
    return frozenset(pages)


async def _load_candidate_chunks(
    session: AsyncSession,
    document_ids: tuple[int, ...],
    nouns: Sequence[str],
) -> list[Chunk]:
    filters = []
    for noun in nouns:
        if len(noun) < 2:
            continue
        filters.append(Chunk.content.contains(noun))
        filters.append(
            (Chunk.section_path.isnot(None)) & (Chunk.section_path.contains(noun))
        )
        if len(noun) >= 4:
            suffix = noun[-4:]
            filters.append(Chunk.content.contains(suffix))
    conditions = [Chunk.document_id.in_(document_ids)]
    type_or_text = [Chunk.chunk_type.in_(_OCR_CHUNK_TYPES)]
    if filters:
        type_or_text.append(or_(*filters))
    conditions.append(or_(*type_or_text))
    stmt = select(Chunk).where(*conditions).order_by(Chunk.document_id, Chunk.page_num)
    return list((await session.execute(stmt)).scalars().all())


def _extract_identifier(content: str) -> Optional[str]:
    for pattern in _ID_PATTERNS:
        match = pattern.search(content)
        if not match:
            continue
        token = (match.group(1) if match.lastindex else match.group(0) or "").strip()
        normalized = normalize_slot_value("identifier", token)
        if normalized:
            return normalized
    return None


def _extract_subject(content: str) -> Optional[str]:
    match = _ORG_RE.search(content or "")
    if not match:
        return None
    return normalize_slot_value("org_name", match.group(1))


def _excerpt(content: str) -> str:
    text = content.replace("\n", " ").strip()
    if len(text) <= _EXCERPT_LIMIT:
        return text
    return text[:_EXCERPT_LIMIT] + "…"


def _row_to_fact(row: MaterialFactRow) -> MaterialFact:
    return MaterialFact(
        canonical_material_key=row.canonical_material_key,
        raw_noun=row.raw_noun,
        grade=row.grade,
        document_id=int(row.document_id),
        bid_part=row.bid_part,
        chunk_id=int(row.chunk_id),
        physical_page=int(row.physical_page or 0),
        identifier=row.identifier,
        subject=row.subject,
        source_channel=row.source_channel,
        excerpt=row.excerpt or "",
    )


def _sql_higher_grade(current, incoming):
    from sqlalchemy import case, literal

    return case(
        (incoming == literal("material"), literal("material")),
        else_=current,
    )


def _sql_fill(current, incoming):
    from sqlalchemy import func

    return func.coalesce(current, incoming)


def _sql_keep_material_excerpt(current_grade, current_excerpt, incoming_grade, incoming_excerpt):
    from sqlalchemy import case, literal

    return case(
        (current_grade == literal("material"), current_excerpt),
        (incoming_grade == literal("material"), incoming_excerpt),
        else_=current_excerpt,
    )


def _sql_keep_channel(current_grade, current_channel, incoming_grade, incoming_channel):
    from sqlalchemy import and_, case, literal

    return case(
        (
            and_(current_grade != literal("material"), incoming_grade == literal("material")),
            incoming_channel,
        ),
        else_=current_channel,
    )
