"""投标信封契约

职责: 投标信封契约——分卷角色档案、齐套/模式冲突判定、信封加载、页码定位格式化
不做: 不上传、不解析、不检索、不调 LLM、不推进项目状态

「信封」= 一个投标审查项目里 doc_type=bid 的全部文档构成的逻辑整体:
- 整本(integral): 恰好 1 份文档, bid_part 为 NULL(存量数据默认属于此形态)
- 分卷(commercial_technical): 商务标 + 技术标各 1 份

档案数据驱动: 新的分卷形态(如两阶段招标的第一/第二信封、正本/副本分册)
只需向 ENVELOPE_PROFILES 增加一条档案, 齐套/冲突/展示逻辑无需改动。
禁止为单个卷别新建专用类型或专用分支(见 generalization-guardrails)。
"""

from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentType

# 信封档案: 模式名 -> 该模式下 bid_part 的完整集合(None 表示整本)
EnvelopeProfiles = dict[str, tuple[Optional[str], ...]]

ENVELOPE_PROFILES: EnvelopeProfiles = {
    "integral": (None,),
    "commercial_technical": ("commercial", "technical"),
}

# 卷角色中文标签(展示用); 未登记的角色回退原始 token, 不阻断渲染
BID_PART_LABELS: dict[str, str] = {
    "commercial": "商务标",
    "technical": "技术标",
}


def _named_parts(profiles: EnvelopeProfiles) -> set[str]:
    """档案中全部具名卷角色(排除整本 None)。"""
    return {
        part
        for parts in profiles.values()
        for part in parts
        if part is not None
    }


def _profile_of_part(
    part: str, profiles: EnvelopeProfiles
) -> Optional[tuple[Optional[str], ...]]:
    """返回包含指定具名卷角色的档案(未登记返回 None)。"""
    for parts in profiles.values():
        if part in parts:
            return parts
    return None


def parse_bid_part(
    value: Optional[str],
    profiles: Optional[EnvelopeProfiles] = None,
) -> Optional[str]:
    """解析 API 入参的卷角色: 空值归一为 None(整本), 非法值抛 ValueError。"""
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    valid = _named_parts(profiles or ENVELOPE_PROFILES)
    if normalized not in valid:
        raise ValueError(
            f"无效的投标分卷类型: {value}, 可选值: {', '.join(sorted(valid))} 或留空(整本)"
        )
    return normalized


def envelope_is_complete(
    parts: Iterable[Optional[str]],
    profiles: Optional[EnvelopeProfiles] = None,
) -> bool:
    """信封是否齐套: 卷角色多重集与某一档案完全一致。

    整本=[None]; 分卷=[commercial, technical]。缺卷/重卷/混装均不齐套。
    """
    actual = sorted("" if p is None else p for p in parts)
    if not actual:
        return False
    for profile_parts in (profiles or ENVELOPE_PROFILES).values():
        expected = sorted("" if p is None else p for p in profile_parts)
        if actual == expected:
            return True
    return False


def envelope_mode_conflict(
    existing_parts: Iterable[Optional[str]],
    new_part: Optional[str],
    profiles: Optional[EnvelopeProfiles] = None,
) -> bool:
    """新上传卷角色与已有信封是否模式冲突。

    冲突形态(换卷别实体仍成立):
    - 整本与任意具名卷混装(None vs 非 None);
    - 具名卷跨档案混装(如商务标 与 第二信封)。
    同一具名卷重复上传不算模式冲突(走同卷替换路径)。
    """
    existing = list(existing_parts)
    if not existing:
        return False
    resolved_profiles = profiles or ENVELOPE_PROFILES
    if new_part is None:
        return any(p is not None for p in existing)
    if any(p is None for p in existing):
        return True
    profile = _profile_of_part(new_part, resolved_profiles)
    if profile is None:
        return True
    return any(p not in profile for p in existing)


@dataclass(frozen=True)
class BidEnvelope:
    """项目投标信封快照(文档按 id 升序, 由 load_bid_envelope 保证)。"""

    documents: tuple[Any, ...]

    @property
    def has_documents(self) -> bool:
        """信封内是否存在任何投标文档。"""
        return bool(self.documents)

    @property
    def document_ids(self) -> tuple[int, ...]:
        """信封内全部文档 id(升序)。"""
        return tuple(doc.id for doc in self.documents)

    @property
    def parts(self) -> tuple[Optional[str], ...]:
        """信封内全部卷角色(与 documents 对位)。"""
        return tuple(getattr(doc, "bid_part", None) for doc in self.documents)

    @property
    def is_complete(self) -> bool:
        """信封是否齐套(见 envelope_is_complete)。"""
        return envelope_is_complete(self.parts)

    @property
    def all_parsed(self) -> bool:
        """信封内全部文档是否均已解析完成。"""
        return self.has_documents and all(
            int(getattr(doc, "parsed", 0) or 0) == 1 for doc in self.documents
        )

    @property
    def primary_document_id(self) -> Optional[int]:
        """主文档 id: 最早落库的一份(整本即其本身), 供无归因回退。"""
        return self.documents[0].id if self.documents else None

    def part_of(self, document_id: int) -> Optional[str]:
        """按文档 id 查卷角色; 不在信封内返回 None。"""
        for doc in self.documents:
            if doc.id == document_id:
                return getattr(doc, "bid_part", None)
        return None

    def part_by_document(self) -> dict[int, Optional[str]]:
        """文档 id -> 卷角色 映射(展示层徽标/页码定位消费)。"""
        return {
            doc.id: getattr(doc, "bid_part", None) for doc in self.documents
        }

    def document_id_for(self, search_results: Sequence[Any]) -> Optional[int]:
        """按最高相似度命中归属文档; 无信封内有效命中回退 primary。"""
        envelope_ids = set(self.document_ids)
        best_id: Optional[int] = None
        best_similarity: Optional[float] = None
        for result in search_results or ():
            doc_id = getattr(result, "document_id", None)
            if doc_id not in envelope_ids:
                continue
            similarity = float(getattr(result, "similarity", None) or 0.0)
            if best_similarity is None or similarity > best_similarity:
                best_similarity = similarity
                best_id = doc_id
        return best_id if best_id is not None else self.primary_document_id


async def load_bid_envelope(db: AsyncSession, project_id: int) -> BidEnvelope:
    """加载项目投标信封(项目内全部 doc_type=bid 文档, 按 id 升序)。"""
    result = await db.execute(
        select(Document)
        .where(
            Document.project_id == project_id,
            Document.doc_type == DocumentType.BID,
        )
        .order_by(Document.id)
    )
    return BidEnvelope(documents=tuple(result.scalars().all()))


def format_page_locations(
    locations: Optional[Sequence[dict]],
    fallback: Optional[str] = None,
) -> str:
    """把页码定位列表格式化为报告文本。

    locations 元素形如 {"document_id": int, "part": str|None, "page": int}。
    分卷: "商务标 P3、P5；技术标 P12"; 整本(part 全为 None): "P3、P5"。
    无定位数据时回退 fallback(历史 source_pages 字符串), 再退 "-"。
    """
    if not locations:
        return str(fallback) if fallback else "-"
    grouped: dict[Optional[str], list[int]] = {}
    for location in locations:
        page = location.get("page")
        if page is None:
            continue
        part = location.get("part")
        pages = grouped.setdefault(part, [])
        if page not in pages:
            pages.append(page)
    if not grouped:
        return str(fallback) if fallback else "-"
    segments: list[str] = []
    for part, pages in grouped.items():
        pages_text = "、".join(f"P{p}" for p in pages)
        if part:
            segments.append(f"{BID_PART_LABELS.get(part, part)} {pages_text}")
        else:
            segments.append(pages_text)
    return "；".join(segments)
