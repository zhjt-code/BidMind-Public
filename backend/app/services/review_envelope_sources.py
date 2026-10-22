"""审查结果的投标信封来源标注

职责: 为审查结果项补充信封来源字段(source_document_id/source_parts/source_page_locations)
不做: 不查数据库、不改审查结论字段、不做前端或报告渲染

定位契约: source_page_locations 元素形如 {"document_id": int, "part": str|None,
"page": int}, 与 core.bid_envelope.format_page_locations 及前端页码跳转共用。
整本信封(或历史数据 bid_part 全空)不产出分卷定位, 消费方回退单文档路径,
保证存量项目展示零变化。
"""

from typing import Any, Optional, Sequence


def attach_envelope_sources(
    item: dict[str, Any],
    *,
    source_document_id: Optional[int],
    retrieval_chunks: Optional[Sequence[dict[str, Any]]],
    part_by_document: dict[int, Optional[str]],
) -> dict[str, Any]:
    """在结果项上就地补充信封来源字段并返回该项。

 Args:
 item: _build_review_result_item 产出的结果字典(须含 source_page_numbers)
 source_document_id: BidResponse.document_id(最高命中归因文档, 可空)
 retrieval_chunks: 检索命中记录 [{page_num, document_id, ...}]
 part_by_document: 信封文档 id -> 卷角色 映射(load_bid_envelope 产出)

 页码归属: 逐页匹配命中 chunk 所在文档(同页跨卷时产出多条定位);
 无命中记录的页回退 source_document_id(a 形态: 锚点必须可回查)。
 """
    item["source_document_id"] = source_document_id
    item["source_parts"] = []
    item["source_page_locations"] = []

    envelope_parts = part_by_document or {}
    # 整本信封(卷角色全空)或历史数据: 不产出分卷定位
    if not any(part for part in envelope_parts.values()):
        return item

    page_numbers = item.get("source_page_numbers") or []
    if not page_numbers:
        return item

    # 页码 -> 命中文档集合(只认信封内文档, 页码为该文档物理页)
    pages_to_docs: dict[int, list[int]] = {}
    for chunk in retrieval_chunks or []:
        page = chunk.get("page_num")
        doc_id = chunk.get("document_id")
        if page is None or doc_id not in envelope_parts:
            continue
        docs = pages_to_docs.setdefault(page, [])
        if doc_id not in docs:
            docs.append(doc_id)

    locations: list[dict[str, Any]] = []
    parts: list[str] = []
    for page in page_numbers:
        doc_ids = sorted(pages_to_docs.get(page, []))
        if not doc_ids and source_document_id is not None:
            doc_ids = [source_document_id]
        for doc_id in doc_ids:
            part = envelope_parts.get(doc_id)
            locations.append(
                {"document_id": doc_id, "part": part, "page": page}
            )
            if part and part not in parts:
                parts.append(part)

    item["source_page_locations"] = locations
    item["source_parts"] = parts
    return item
