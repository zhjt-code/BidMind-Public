"""语义检索器

基于 PGVector 实现语义检索，支持多种检索策略。
"""

from dataclasses import dataclass
from typing import Optional

from loguru import logger
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.parser.image_extractor import (
    IMAGE_EVIDENCE_MARKER,
    is_image_related_chunk_content,
)
from app.core.vectorizer import VectorIndexer
from app.models.chunk import Chunk
from app.models.document import Document, DocumentType


@dataclass
class SearchResult:
    """检索结果

    Fix-02 新增字段（均可选，默认 None，不影响旧调用方）：
    - ``matched_sub_query``：QueryDecomposer 分解后命中的子查询文本
    - ``matched_keywords``：deep 搜索命中所用关键词列表
    - ``role``：命中来源角色（primary / image / toc / deep），供持久化到 bid_response_chunks
    """
    chunk_id: int
    document_id: int
    content: str
    page_num: Optional[int]
    similarity: float
    doc_type: Optional[DocumentType] = None
    chunk_type: str = "text"
    matched_sub_query: Optional[str] = None
    matched_keywords: Optional[list[str]] = None
    role: Optional[str] = None

    @classmethod
    def from_chunk(
        cls,
        chunk: Chunk,
        similarity: float,
        doc_type: Optional[DocumentType] = None,
        *,
        matched_sub_query: Optional[str] = None,
        matched_keywords: Optional[list[str]] = None,
        role: Optional[str] = None,
    ) -> "SearchResult":
        """从 Chunk 对象创建检索结果

        Args:
            chunk: 分块对象
            similarity: 相似度
            doc_type: 文档类型
            matched_sub_query: 命中所属子查询文本（Fix-02）
            matched_keywords: 命中所用关键词列表（Fix-02）
            role: 命中来源角色（Fix-02，primary/image/toc/deep）

        Returns:
            SearchResult: 检索结果
        """
        return cls(
            chunk_id=chunk.id,
            document_id=chunk.document_id,
            content=chunk.content,
            page_num=chunk.page_num,
            similarity=similarity,
            doc_type=doc_type,
            chunk_type=getattr(chunk, "chunk_type", "text") or "text",
            matched_sub_query=matched_sub_query,
            matched_keywords=matched_keywords,
            role=role,
        )


class SemanticRetriever:
    """语义检索器
    
    提供多种语义检索策略：
    - 按文档检索：在指定文档中检索
    - 按项目检索：在项目所有文档中检索
    - 按文档类型检索：在指定类型文档中检索（招标/投标）
    
    示例:
        >>> retriever = SemanticRetriever(db)
        >>> results = await retriever.search_in_document(doc_id, "技术要求", top_k=5)
    """
    
    def __init__(self, db: AsyncSession):
        """初始化语义检索器
        
        Args:
            db: 数据库会话
        """
        self.db = db
        self.indexer = VectorIndexer(db)
    
    async def search_in_document(
        self,
        document_id: int,
        query: str,
        top_k: int = 5,
        min_similarity: float = 0.0
    ) -> list[SearchResult]:
        """在指定文档中检索
        
        Args:
            document_id: 文档ID
            query: 查询文本
            top_k: 返回结果数量
            min_similarity: 最小相似度阈值
            
        Returns:
            list[SearchResult]: 检索结果列表
        """
        results = await self.indexer.search(
            query=query,
            document_id=document_id,
            top_k=top_k,
            min_similarity=min_similarity
        )
        
        # 获取文档类型
        doc = await self._get_document(document_id)
        doc_type = doc.doc_type if doc else None
        
        return [
            SearchResult.from_chunk(chunk, similarity, doc_type)
            for chunk, similarity in results
        ]
    
    async def search_in_project(
        self,
        project_id: int,
        query: str,
        top_k: int = 10,
        min_similarity: float = 0.0,
        doc_type: Optional[DocumentType] = None
    ) -> list[SearchResult]:
        """在项目中检索
        
        Args:
            project_id: 项目ID
            query: 查询文本
            top_k: 返回结果数量
            min_similarity: 最小相似度阈值
            doc_type: 限定文档类型（可选）
            
        Returns:
            list[SearchResult]: 检索结果列表
        """
        # 获取项目的文档
        documents = await self._get_project_documents(project_id, doc_type)
        
        if not documents:
            logger.warning(f"项目 {project_id} 没有可检索的文档")
            return []
        
        # 在每个文档中检索
        all_results: list[SearchResult] = []
        
        for doc in documents:
            results = await self.indexer.search(
                query=query,
                document_id=doc.id,
                top_k=top_k,
                min_similarity=min_similarity
            )
            
            for chunk, similarity in results:
                all_results.append(
                    SearchResult.from_chunk(chunk, similarity, doc.doc_type)
                )
        
        # 按相似度排序并截取 top_k
        all_results.sort(key=lambda x: x.similarity, reverse=True)
        
        return all_results[:top_k]
    
    async def search_tender(
        self,
        project_id: int,
        query: str,
        top_k: int = 5,
        min_similarity: float = 0.0
    ) -> list[SearchResult]:
        """在招标文件中检索
        
        Args:
            project_id: 项目ID
            query: 查询文本
            top_k: 返回结果数量
            min_similarity: 最小相似度阈值
            
        Returns:
            list[SearchResult]: 检索结果列表
        """
        return await self.search_in_project(
            project_id=project_id,
            query=query,
            top_k=top_k,
            min_similarity=min_similarity,
            doc_type=DocumentType.TENDER
        )
    
    async def search_bid(
        self,
        project_id: int,
        query: str,
        top_k: int = 5,
        min_similarity: float = 0.0
    ) -> list[SearchResult]:
        """在投标文件中检索
        
        Args:
            project_id: 项目ID
            query: 查询文本
            top_k: 返回结果数量
            min_similarity: 最小相似度阈值
            
        Returns:
            list[SearchResult]: 检索结果列表
        """
        return await self.search_in_project(
            project_id=project_id,
            query=query,
            top_k=top_k,
            min_similarity=min_similarity,
            doc_type=DocumentType.BID
        )
    
    async def find_related_bid_content(
        self,
        project_id: int,
        tender_requirement: str,
        top_k: int = 5
    ) -> list[SearchResult]:
        """根据招标要求查找相关的投标内容
        
        这是核心业务场景：给定一条招标要求，在投标文件中
        找到最相关的响应内容。
        
        Args:
            project_id: 项目ID
            tender_requirement: 招标要求内容
            top_k: 返回结果数量
            
        Returns:
            list[SearchResult]: 投标文件中的相关内容
        """
        logger.debug(
            f"查找投标响应: project={project_id}, "
            f"requirement='{tender_requirement[:50]}...'"
        )
        
        return await self.search_bid(
            project_id=project_id,
            query=tender_requirement,
            top_k=top_k,
            min_similarity=0.3  # 设置较低阈值，确保有结果
        )
    
    async def fetch_image_chunks_by_pages(
        self,
        document_id: int,
        page_nums: set[int],
        expand_range: int = 1,
        existing_chunk_ids: set[int] | None = None,
    ) -> list[SearchResult]:
        """按页码获取包含图片标注的分块

        语义检索可能遗漏图片标注chunk(结构化文本与自然语言的向量距离大)。
        本方法根据已检索到的页码, 主动补充相邻页面的图片证据。

        Args:
            document_id: 文档ID
            page_nums: 已检索到的页码集合
            expand_range: 向前后扩展的页码数(默认1, 即 +-1 页)
            existing_chunk_ids: 已有的chunk ID集合, 用于去重

        Returns:
            list[SearchResult]: 包含图片标注的额外分块
        """
        if not page_nums:
            return []

        expanded = set()
        for p in page_nums:
            for offset in range(-expand_range, expand_range + 1):
                if p + offset > 0:
                    expanded.add(p + offset)

        exclude_ids = existing_chunk_ids or set()

        stmt = (
            select(Chunk)
            .where(
                Chunk.document_id == document_id,
                Chunk.page_num.in_(expanded),
                or_(
                    Chunk.chunk_type.in_(("image_annotation", "image_evidence")),
                    Chunk.content.contains("[图片"),
                    Chunk.content.contains(IMAGE_EVIDENCE_MARKER),
                ),
            )
            .order_by(Chunk.page_num, Chunk.chunk_index)
        )
        result = await self.db.execute(stmt)
        chunks = list(result.scalars().all())

        doc = await self._get_document(document_id)
        doc_type = doc.doc_type if doc else None

        image_results: list[SearchResult] = []
        for chunk in chunks:
            if chunk.id in exclude_ids:
                continue
            image_results.append(
                SearchResult.from_chunk(chunk, similarity=0.0, doc_type=doc_type)
            )

        if image_results:
            logger.debug(
                f"图片证据补充: document_id={document_id}, "
                f"页码={sorted(expanded)}, 补充{len(image_results)}个图片chunk"
            )

        return image_results

    async def fetch_image_chunks_globally(
        self,
        document_id: int,
        keywords: list[str],
        max_results: int = 15,
        query: Optional[str] = None,
    ) -> list[SearchResult]:
        """全文档范围搜索包含图片标注且匹配任一关键词的chunk

 用于二次深搜: 当首轮语义检索未命中时,基于关键词在整个
 文档中查找包含图片证据的分块。: 提供 query 时扩大候选池并按查询相关性重排后截断,
 替代纯页码序截断(后者系统性偏向文档前部,无关扫描件会挤掉
 文档后部的真实证据)。

 Args:
 document_id: 文档ID
 keywords: 关键词列表(OR匹配)
 max_results: 最大返回数
 query: 查询文本(通常为招标要求原文),提供时按相关性截断

 Returns:
 list[SearchResult]: 匹配的图片证据分块
 """
        if not keywords:
            return []

        keyword_filters = [Chunk.content.contains(kw) for kw in keywords[:10]]
        base_conditions = [
            Chunk.document_id == document_id,
            or_(
                Chunk.chunk_type.in_(("image_annotation", "image_evidence")),
                Chunk.content.contains("[图片"),
                Chunk.content.contains(IMAGE_EVIDENCE_MARKER),
            ),
            or_(*keyword_filters),
        ]
        # query 提供时委托 global_evidence_search 完成"先排序后限额"
        # (向量距离排序取 max_results*4); query 为空时其内部保持页码序旧行为
        from app.core.retriever.global_evidence_search import rank_global_candidates

        chunks = await rank_global_candidates(
            self.db, base_conditions, query, max_results
        )

        doc = await self._get_document(document_id)
        doc_type = doc.doc_type if doc else None

        image_results: list[SearchResult] = []
        for chunk in chunks:
            image_results.append(
                SearchResult.from_chunk(chunk, similarity=0.0, doc_type=doc_type)
            )

        if query and len(image_results) > max_results:
            from app.core.retriever.evidence_ranker import rerank_results_by_query

            image_results = await rerank_results_by_query(
                self.db,
                image_results,
                query,
                limit=max_results,
                min_rerank_size=2,
            )
        elif query and image_results:
            from app.core.retriever.evidence_ranker import dedupe_by_content_prefix

            image_results = dedupe_by_content_prefix(image_results)

        if image_results:
            logger.debug(
                f"全文档图片证据搜索: document_id={document_id}, "
                f"keywords={keywords[:5]}, 命中{len(image_results)}个chunk"
            )

        return image_results

    async def fetch_keyword_chunks_globally(
        self,
        document_id: int,
        keywords: list[str],
        max_results: int = 15,
        query: Optional[str] = None,
        chunk_types: Optional[tuple[str, ...]] = None,
    ) -> list[SearchResult]:
        """全文档搜索结构化文本、表格与图片证据chunk。

        用于未响应兜底: 表格页可能不是图片证据,但可通过表名或章节路径命中。
        """
        terms = [kw for kw in keywords[:12] if kw and len(kw) >= 2]
        if not terms:
            return []

        target_types = chunk_types or (
            "table_row_with_header",
            "table",
            "clause",
            "paragraph",
            "image_evidence",
            "image_annotation",
        )
        keyword_filters = [
            or_(Chunk.content.contains(kw), Chunk.section_path.contains(kw))
            for kw in terms
        ]
        base_conditions = [
            Chunk.document_id == document_id,
            Chunk.chunk_type.in_(target_types),
            or_(*keyword_filters),
        ]
        # 同 fetch_image_chunks_globally, 排序前移到 SQL 层
        from app.core.retriever.global_evidence_search import rank_global_candidates

        chunks = await rank_global_candidates(
            self.db, base_conditions, query, max_results
        )

        doc = await self._get_document(document_id)
        doc_type = doc.doc_type if doc else None
        results = [
            SearchResult.from_chunk(chunk, similarity=0.0, doc_type=doc_type)
            for chunk in chunks
        ]

        if query and len(results) > max_results:
            from app.core.retriever.evidence_ranker import rerank_results_by_query

            results = await rerank_results_by_query(
                self.db,
                results,
                query,
                limit=max_results,
                min_rerank_size=2,
            )
        elif query and results:
            from app.core.retriever.evidence_ranker import dedupe_by_content_prefix

            results = dedupe_by_content_prefix(results)[:max_results]

        if results:
            logger.debug(
                f"全文档结构化证据搜索: document_id={document_id}, "
                f"keywords={terms[:5]}, 命中{len(results)}个chunk"
            )

        return results

    @staticmethod
    def is_image_related_result(result: SearchResult) -> bool:
        """统一判断检索结果是否属于图片相关证据。"""
        if getattr(result, "chunk_type", "") in {"image_annotation", "image_evidence"}:
            return True
        return is_image_related_chunk_content(result.content)

    async def _get_document(self, document_id: int) -> Optional[Document]:
        """获取文档"""
        result = await self.db.execute(
            select(Document).where(Document.id == document_id)
        )
        return result.scalar_one_or_none()
    
    async def _get_project_documents(
        self,
        project_id: int,
        doc_type: Optional[DocumentType] = None
    ) -> list[Document]:
        """获取项目的文档"""
        query = select(Document).where(
            Document.project_id == project_id,
            Document.parsed == 1  # 只检索已解析的文档
        )
        
        if doc_type:
            query = query.where(Document.doc_type == doc_type)
        
        result = await self.db.execute(query)
        return list(result.scalars().all())
