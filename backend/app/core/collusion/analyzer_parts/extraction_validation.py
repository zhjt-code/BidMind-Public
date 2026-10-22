"""串标分析器抽取域纯函数

从 analyzer.py 迁出抽取相关的 8 个纯函数。
主 analyzer.py 顶部 re-export 保持外部 import 路径零变化。

收纳范围：
- 空值/校验判断：_is_empty_extraction / _validate_extraction_payload
- 结构信息提取：_extract_docx_structure_from_info
- chunk 文本拼接：_build_chunk_text_blob / _build_chunk_fallback_context
- 抽取结果工厂/格式化：_empty_extraction / _format_extraction
- 文档子类型推断：_infer_doc_subtype

设计原则：
- 全部为纯函数，无 self 依赖
- 仅依赖 logger + json + extraction_routes.build_empty_extraction
- _validate_extraction_payload 内部 lazy import CollusionExtractionPayload
 保留原行为（兼容 锁定语义）
"""

from __future__ import annotations

import json

from loguru import logger

from app.core.collusion.extraction_routes import build_empty_extraction


def _is_empty_extraction(ext: dict) -> bool:
    """判断提取结果是否实质为空"""
    if not ext:
        return True
    non_empty = 0
    for key in ("company_info", "personnel_list", "price_info",
                "technical_highlights", "qualifications", "past_projects"):
        val = ext.get(key)
        if val and (not isinstance(val, list) or len(val) > 0):
            non_empty += 1
    return non_empty == 0


def _validate_extraction_payload(
    extraction: dict,
    *,
    document_id: int,
    doc_type: str,
) -> None:
    """: 对完整 extraction dict 做顶层 Pydantic 校验（仅 warning 不阻断）。

 目的是在写入 DB 前捕捉字段类型异常 / 未知子键 / LLM 输出漂移，把隐性
 契约违规显式暴露为日志。此函数不修改传入 dict（校验只读），失败时记录 warning
 后由调用方继续原有写入流程，保证向后兼容。

 调用点: `_save_extraction`；覆盖 bid + tender 两条路径。
 """
    if not extraction:
        return
    try:
        from app.schemas.collusion import CollusionExtractionPayload
        CollusionExtractionPayload.model_validate(extraction)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"[Analyzer] extraction payload schema 校验失败 "
            f"doc_id={document_id} doc_type={doc_type} err={exc}"
        )


def _extract_docx_structure_from_info(info: dict | None) -> dict | None:
    """S.2.8 (D-025 方案 c): 从 extraction 结果里提取 docx 结构特征。

    当前 extractor 尚未生产 docx_structure 字段，此函数返回 None，
    D6FormatAlgorithmicGate 会走 "结构信息不可用 → verdict=pass, confidence=0" 兜底。
    未来若 extractor 补采 toc_headings / font_families / margins / headers_footers，
    只需将其挂到 ext["docx_structure"] 或 ext["docx_format"]，本函数自动返回真实结构。
    """
    if not isinstance(info, dict):
        return None
    for key in ("docx_structure", "docx_format", "document_structure"):
        val = info.get(key)
        if isinstance(val, dict) and val:
            return val
    return None


def _build_chunk_text_blob(
    chunk_texts: dict[int, list[tuple[int | None, str]]] | None,
    doc_id: int,
    max_chars: int = 10000,
) -> str:
    """拼接指定文档的 chunk 文本，输出 D7AbnormalConsistencyGate 所需的整体文本视图。

    背景: ``_prefetch_chunks_for_gate`` 已经按文档分桶并把每 chunk 截断到 1500 字符，
    但 ``D7AbnormalConsistencyGate`` 仅读取 ``evidence["text_a"] / evidence["text_b"]``。
    本 helper 把 chunk 序列按页顺序拼接，并按 ``max_chars`` 上限截断以约束算法成本。

    设计取舍:
      - 默认 ``max_chars=10000``：足以驱动 LCS 滑动窗口（窗口 200 / 步长 100）和共同
        错别字检测，同时单对算法成本可控（≈100x100 fuzz.ratio 比较）。
      - 缺失文档 / 空列表 / 空内容均返回 ``""``，让 D7 走 "文本不可用" 兜底分支。
      - ``max_chars <= 0`` 视为禁用，直接返回 ``""``，避免歧义切片行为。
    """
    if not isinstance(chunk_texts, dict) or max_chars <= 0:
        return ""
    entries = chunk_texts.get(doc_id) or []
    if not entries:
        return ""

    parts: list[str] = []
    used = 0
    for _, content in entries:
        if not content:
            continue
        remaining = max_chars - used
        if remaining <= 0:
            break
        if len(content) > remaining:
            parts.append(content[:remaining])
            used = max_chars
            break
        parts.append(content)
        used += len(content)

    # 末尾 slice 守护: 拼接分隔符 "\n" 不计入预算 budget，
    # 这里硬切到 max_chars 以维持严格上限，防止下游算法成本越界。
    return "\n".join(parts)[:max_chars]


def _build_chunk_fallback_context(
    similar_chunks: list[dict],
    doc_side: str,
    max_chars: int = 6000,
) -> str:
    """从相似chunk中提取指定文档侧的原文作为兜底上下文"""
    text_key = f"text_{doc_side}"
    page_key = f"page_{doc_side}"
    seen: set[str] = set()
    parts: list[str] = []
    total = 0
    for chunk in similar_chunks:
        text = chunk.get(text_key, "")
        if not text or text in seen:
            continue
        seen.add(text)
        page = chunk.get(page_key)
        prefix = f"(第{page}页) " if page else ""
        part = f"{prefix}{text}"
        if total + len(part) > max_chars:
            break
        parts.append(part)
        total += len(part)
    return "\n".join(parts) if parts else ""


def _empty_extraction() -> dict:
    return build_empty_extraction()


def _format_extraction(ext: dict) -> str:
    """将提取结果格式化为 LLM 可读的文本"""
    return json.dumps(ext, ensure_ascii=False, indent=2)


def _infer_doc_subtype(extraction: dict, doc_filename: str) -> str:
    """步骤6: 推断文档子类型

    Returns: "commercial" / "technical" / "pricing" / "mixed"
    """
    name_lower = (doc_filename or "").lower()

    if any(kw in name_lower for kw in ("商务", "资信", "资格")):
        return "commercial"
    if any(kw in name_lower for kw in ("技术",)):
        return "technical"
    if any(kw in name_lower for kw in ("报价", "价格", "经济", "商务报价")):
        return "pricing"

    if not extraction:
        return "mixed"

    tech = extraction.get("technical_highlights") or []
    quals = extraction.get("qualifications") or []
    projects = extraction.get("past_projects") or []
    price = extraction.get("price_info") or {}
    has_price = bool(price.get("total_price"))
    has_tech = bool(tech and any(str(t).strip() for t in tech))
    has_quals = bool(quals and any(str(q).strip() for q in quals))
    has_projects = bool(projects and any(str(p).strip() for p in projects))

    if not has_tech and (has_quals or has_projects):
        return "commercial"
    if has_price and not has_tech:
        return "pricing"
    if has_tech and not has_quals and not has_projects:
        return "technical"

    return "mixed"
