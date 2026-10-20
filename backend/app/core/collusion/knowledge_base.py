"""串标弱权重知识库加载与辅助判断。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

_KB_DIR = Path(__file__).with_name("kb")


def _load_json(filename: str) -> dict[str, Any]:
    path = _KB_DIR / filename
    with path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


@lru_cache(maxsize=1)
def get_normal_similarity_rules() -> dict[str, Any]:
    return _load_json("normal_similarity_rules.json")


@lru_cache(maxsize=1)
def get_entity_normalization_rules() -> dict[str, Any]:
    return _load_json("entity_normalization_rules.json")


@lru_cache(maxsize=1)
def get_industry_hints() -> dict[str, Any]:
    return _load_json("industry_hints.json")


def is_known_normal_similarity(text: str) -> bool:
    """判断文本是否命中通用白名单规则。"""
    normalized = str(text or "").strip()
    if not normalized:
        return False
    rules = get_normal_similarity_rules()
    for phrases in rules.values():
        for phrase in phrases:
            if phrase and phrase in normalized:
                return True
    return False


# 通用白名单内部 key → 中文 label 单一映射真源
_NORMAL_SIMILARITY_CATEGORY_LABELS: dict[str, str] = {
    "legalBoilerplate": "法规/固定法律条文",
    "formatTemplates": "通用格式模板",
    "constructionCommon": "工程类通用章节",
    "itServiceCommon": "IT服务类通用章节",
}


def extract_matched_categories(chunk_pairs: list[dict]) -> list[str]:
    """从 chunk pair 列表中提取命中的通用白名单类别（中文 label）。

    供 pair_compare 写入 quality_flags 与 narrative_builder 拼装事实段复用，
    与 ``build_normal_similarity_hint`` 共享同一类别提取规则。

    Args:
        chunk_pairs: 形如 ``[{"text_a": "...", "text_b": "..."}, ...]`` 的列表
            （兼容 similarity_chunks 输出格式）。

    Returns:
        命中的类别中文 label 列表（按 key 字典序排序），未命中返回空列表。
    """
    matched_categories: set[str] = set()
    rules = get_normal_similarity_rules()
    for pair in chunk_pairs or []:
        if not isinstance(pair, dict):
            continue
        combined = f"{pair.get('text_a', '')}\n{pair.get('text_b', '')}"
        for category, phrases in rules.items():
            if any(phrase and phrase in combined for phrase in phrases):
                matched_categories.add(category)
    if not matched_categories:
        return []
    return [
        _NORMAL_SIMILARITY_CATEGORY_LABELS.get(item, item)
        for item in sorted(matched_categories)
    ]


def build_normal_similarity_hint(chunk_pairs: list[dict]) -> str:
    """为 Prompt 构造弱权重白名单提示。"""
    categories = extract_matched_categories(chunk_pairs)
    if not categories:
        return ""
    readable = "、".join(categories)
    return (
        "## 弱知识库提示\n"
        f"以下相似内容命中了通用白名单类别：{readable}。"
        "这些内容更可能属于模板、法规、行业通用章节，请降低 D3/D6 certainty，"
        "不得仅凭其相似性直接提升高风险。"
    )


async def build_dynamic_normal_similarity_hint(
    db: AsyncSession,
    chunk_pairs: list[dict],
    *,
    owner_id: int | None = None,
    allowed_document_ids: list[int] | None = None,
    top_k: int = 3,
) -> str:
    """基于动态知识库构造串标弱提示。

    动态知识库只用于豁免/解释正常相似，不得作为串标定性证据。

    Args:
        allowed_document_ids: 用户在串标上传页显式授权的知识资料 IDs; 未传入或
            为空列表表示项目未启用动态知识库, 必须返回空字符串, 防止默认召回。
    """
    if allowed_document_ids is not None and not allowed_document_ids:
        return ""
    query_parts: list[str] = []
    for pair in chunk_pairs[:5]:
        query_parts.append(str(pair.get("text_a") or "")[:500])
        query_parts.append(str(pair.get("text_b") or "")[:500])
    query = "\n".join(part for part in query_parts if part.strip())
    if not query.strip():
        return ""
    try:
        from app.services.knowledge_base_service import KnowledgeService

        refs = await KnowledgeService(db).search(
            query=query,
            owner_id=owner_id,
            applicable_flow="collusion_check",
            business_types=[],
            allowed_document_ids=allowed_document_ids,
            top_k=top_k,
            min_similarity=0.5,
        )
    except Exception:
        return ""
    if not refs:
        return ""
    lines = [
        "## 动态知识库弱提示",
        "以下知识库内容仅可用于正常相似豁免或行业通用解释，禁止作为串标定性依据或风险等级提升依据。",
    ]
    for ref in refs:
        lines.append(
            f"- kb_doc_id={ref.kb_doc_id}, chunk_id={ref.kb_chunk_id}, "
            f"page={ref.page_num or '未知'}, authority={ref.authority}, "
            f"version={ref.version}, similarity={ref.similarity:.3f}: "
            f"{ref.content[:500]}"
        )
    return "\n".join(lines)
