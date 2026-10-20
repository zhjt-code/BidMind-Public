"""向量化模块

提供文本向量化和PGVector存储功能。

核心组件:
- EmbeddingModel: 基于bge-m3的文本向量化
- VectorIndexer: 向量存储和检索
"""

from app.core.vectorizer.embedding import EmbeddingModel, get_embedding_model
from app.core.vectorizer.indexer import VectorIndexer

__all__ = [
    "EmbeddingModel",
    "get_embedding_model",
    "VectorIndexer",
]
