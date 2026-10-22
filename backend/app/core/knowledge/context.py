"""知识库检索上下文门控。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.schemas.knowledge_base import KnowledgeChunkReference
from app.services.knowledge_base_service import KnowledgeService


@dataclass(frozen=True)
class KnowledgeContext:
    """经过门控后的知识库上下文。"""

    text: str
    references: list[KnowledgeChunkReference] = field(default_factory=list)
    conflict_note: Optional[str] = None


class KnowledgeContextBuilder:
    """统一构建 LLM Prompt 的知识库参考上下文。"""

    def __init__(
        self,
        db: AsyncSession,
        *,
        min_similarity: float = 0.5,
        token_budget_ratio: float = 0.25,
        chars_per_token: int = 2,
    ):
        self.db = db
        self.min_similarity = min_similarity
        self.token_budget_ratio = token_budget_ratio
        self.chars_per_token = chars_per_token

    async def build(
        self,
        *,
        query: str,
        flow: str,
        current_user: Optional[User] = None,
        owner_id: Optional[int] = None,
        primary_text: str = "",
        prompt_token_budget: int = 6000,
        business_types: Optional[list[str]] = None,
        allowed_document_ids: Optional[list[int]] = None,
        top_k: int = 5,
    ) -> KnowledgeContext:
        """构建有来源、有预算、有冲突提示的知识库上下文。

        Args:
            allowed_document_ids: 项目级显式授权的知识资料 ID 列表; 仅当用户在
                流程入口启用并选择资料后, 调用方才会传入。未传或为空时直接返回
                空上下文, 严防默认自动召回污染主流程 Prompt。
        """
        max_chars = max(int(prompt_token_budget * self.token_budget_ratio * self.chars_per_token), 300)
        if allowed_document_ids is not None and not allowed_document_ids:
            return KnowledgeContext(text="", references=[])
        results = await KnowledgeService(self.db).search(
            query=query,
            current_user=current_user,
            owner_id=owner_id,
            applicable_flow=flow,
            business_types=business_types or [],
            allowed_document_ids=allowed_document_ids,
            top_k=top_k,
            min_similarity=self.min_similarity,
        )
        if not results:
            return KnowledgeContext(text="", references=[])

        lines = [
            "\n## 知识库参考（辅助信息）",
            "以下内容仅用于解释规范、行业常识或素材复用；如与招标文件冲突，必须以招标文件为准。",
        ]
        used_refs: list[KnowledgeChunkReference] = []
        used_chars = 0
        for item in results:
            excerpt = item.content.strip().replace("\x00", "")
            if not excerpt:
                continue
            remain = max_chars - used_chars
            if remain <= 0:
                break
            excerpt = excerpt[:remain]
            used_chars += len(excerpt)
            used_refs.append(item)
            lines.append(
                f"- 来源 kb_doc_id={item.kb_doc_id}, chunk_id={item.kb_chunk_id}, "
                f"page={item.page_num or '未知'}, authority={item.authority}, "
                f"version={item.version}, similarity={item.similarity:.3f}: {excerpt}"
            )

        conflict_note = self._detect_conflict(primary_text, used_refs)
        if conflict_note:
            lines.append(f"\n【冲突提示】{conflict_note}。处理时必须以招标文件或项目内事实为准。")
        return KnowledgeContext(text="\n".join(lines), references=used_refs, conflict_note=conflict_note)

    def _detect_conflict(
        self,
        primary_text: str,
        refs: list[KnowledgeChunkReference],
    ) -> Optional[str]:
        """轻量冲突提示：只做门控提示，不做自动裁决。"""
        if not primary_text or not refs:
            return None
        primary = primary_text[:2000]
        negative_markers = ("不得", "禁止", "不允许", "严禁")
        positive_markers = ("必须", "应当", "需要", "须")
        primary_negative = any(marker in primary for marker in negative_markers)
        primary_positive = any(marker in primary for marker in positive_markers)
        ref_text = "\n".join(ref.content[:1000] for ref in refs)
        ref_negative = any(marker in ref_text for marker in negative_markers)
        ref_positive = any(marker in ref_text for marker in positive_markers)
        if (primary_negative and ref_positive) or (primary_positive and ref_negative):
            return "知识库参考与招标/项目文本存在疑似强制性表述冲突"
        return None
