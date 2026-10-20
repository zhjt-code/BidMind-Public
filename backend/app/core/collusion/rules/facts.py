"""
职责: 从单份投标文件的提取结果判定各事实字段是否已获得。
不做: 跨文件比对、规则命中、风险判定。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from app.core.collusion.identifier_index import build_index
from app.core.collusion.rules.contracts import FACT_FIELDS
from app.schemas.collusion import is_valid_person_name

# collectable=False 的事实名集合（一律不进 present_facts）
_NON_COLLECTABLE: frozenset[str] = frozenset(
    k for k, v in FACT_FIELDS.items() if not v.collectable
)


@dataclass(frozen=True)
class DocumentFacts:
    """单份投标文件的事实字段采集情况快照（不可变）。"""

    doc_id: int
    doc_name: str
    present_facts: frozenset[str]
    identifier_index: object | None


def _is_nonempty_str(val: Any) -> bool:
    """字符串类型且 strip 后非空。"""
    return isinstance(val, str) and bool(val.strip())


def _is_nonempty_list(val: Any) -> bool:
    """列表类型且至少有一个非空元素（str 需 strip 后非空；非 str 只需非 None）。"""
    if not isinstance(val, list) or not val:
        return False
    for elem in val:
        if isinstance(elem, str):
            if elem.strip():
                return True
        elif elem is not None:
            return True
    return False


def _collect_company_facts(company_info: dict[str, Any], present: set[str]) -> None:
    """从 company_info 采集公司级事实。"""
    simple_fields = (
        "company_name", "legal_representative", "unified_social_credit_code",
        "contact_phone", "registered_address", "bank_account",
    )
    for fname in simple_fields:
        if _is_nonempty_str(company_info.get(fname)):
            present.add(fname)


def _collect_personnel_facts(personnel_list: list[Any], present: set[str]) -> None:
    """从 personnel_list 采集人员级事实（人名须通过 is_valid_person_name）。"""
    has_name = has_role = has_cert = has_id = False
    for person in personnel_list:
        if not isinstance(person, dict):
            continue
        name = str(person.get("name") or "").strip()
        if not has_name and name and is_valid_person_name(name):
            has_name = True
        if not has_role and _is_nonempty_str(person.get("role")):
            has_role = True
        certs = person.get("certificate_numbers") or []
        if not has_cert and _is_nonempty_list(certs):
            has_cert = True
        id_num = str(person.get("id_number") or "").strip()
        if not has_id and id_num:
            has_id = True
    if has_name:
        present.add("personnel_names")
    if has_role:
        present.add("personnel_roles")
    if has_cert:
        present.add("personnel_certs")
    if has_id:
        present.add("personnel_id_tails")


def _collect_metadata_facts(meta: dict[str, Any], present: set[str]) -> None:
    """从 metadata_info 采集 PDF 元数据事实。"""
    simple_map = {
        "author": "pdf_author",
        "creator_tool": "pdf_creator_tool",
        "producer": "pdf_producer",
        "creation_date": "pdf_creation_date",
        "mod_date": "pdf_mod_date",
    }
    for src_key, fact_name in simple_map.items():
        if _is_nonempty_str(meta.get(src_key)):
            present.add(fact_name)
    # subject 或 keywords 任一非空即视为 pdf_subject_keywords 存在
    if _is_nonempty_str(meta.get("subject")) or _is_nonempty_str(meta.get("keywords")):
        present.add("pdf_subject_keywords")


def build_document_facts(
    doc_id: int,
    doc_name: str,
    extraction: dict[str, Any] | None,
    *,
    has_text_chunks: bool = True,
) -> DocumentFacts:
    """从提取结果字典构建文档事实快照。

    Args:
        doc_id: 文档 ID
        doc_name: 文档显示名
        extraction: CollusionExtraction 的 dict 形态；None/非 dict/空 dict 均容忍
        has_text_chunks: 文档是否已有正文分块（进串标对比的文档默认为 True）

    Returns:
        DocumentFacts，collectable=False 的事实绝不出现在 present_facts 中
    """
    if not isinstance(extraction, dict) or not extraction:
        return DocumentFacts(
            doc_id=doc_id,
            doc_name=doc_name,
            present_facts=frozenset(),
            identifier_index=None,
        )

    # 构建标识符索引（失败时降级为 None，不抛穿）
    identifier_index: object | None = None
    try:
        identifier_index = build_index(doc_id, doc_name, extraction)
    except Exception as exc:
        logger.warning("build_index 失败，置 None: doc_id={}, error={}", doc_id, exc)

    present: set[str] = set()

    # company_info 事实
    company_info = extraction.get("company_info") or {}
    if isinstance(company_info, dict):
        _collect_company_facts(company_info, present)

    # personnel_list 事实
    personnel_list = extraction.get("personnel_list") or []
    if isinstance(personnel_list, list):
        _collect_personnel_facts(personnel_list, present)

    # 投标总价
    price_info = extraction.get("price_info") or {}
    if isinstance(price_info, dict) and _is_nonempty_str(price_info.get("total_price")):
        present.add("total_price")

    # 顶层列表字段
    for fact_name in ("qualifications", "past_projects", "subcontractors", "technical_highlights"):
        if _is_nonempty_list(extraction.get(fact_name)):
            present.add(fact_name)

    # 文档正文分块（由调用方断言，能进串标对比的文档必然已有 chunk）
    if has_text_chunks:
        present.add("text_chunks")

    # 文档结构指纹
    docx_struct = extraction.get("docx_structure")
    if isinstance(docx_struct, dict) and docx_struct:
        present.add("docx_structure")

    # metadata_info 事实
    meta = extraction.get("metadata_info") or {}
    if isinstance(meta, dict):
        _collect_metadata_facts(meta, present)

    # 过滤掉所有 collectable=False 的事实（单一守卫，防止上层疏漏）
    present -= _NON_COLLECTABLE

    return DocumentFacts(
        doc_id=doc_id,
        doc_name=doc_name,
        present_facts=frozenset(present),
        identifier_index=identifier_index,
    )
