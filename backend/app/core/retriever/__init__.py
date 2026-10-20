"""语义检索模块

提供基于向量相似度的语义检索功能。
"""

from app.core.retriever.semantic_retriever import SearchResult, SemanticRetriever

__all__ = ["SemanticRetriever", "SearchResult"]
