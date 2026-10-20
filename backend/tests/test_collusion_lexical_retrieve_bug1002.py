""": 串标维度检索改为词项通道, 不再用 contains+limit(5) 独撑。"""

from __future__ import annotations

import inspect
import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")

from app.core.collusion.analyzer_parts import pair_compare, pair_dimensions
from app.core.retriever.evidence_pipeline.dimension_context import (
    collect_dimension_context_text,
)


def test_retrieve_dimension_context_delegates_to_lexical_pipeline() -> None:
    src = inspect.getsource(pair_compare.retrieve_dimension_context)
    assert "content.contains" not in src
    assert ".limit(5)" not in src
    assert "collect_dimension_context_text" in src


def test_dimension_context_uses_lexical_retriever() -> None:
    src = inspect.getsource(collect_dimension_context_text)
    assert "search_lexical_in_document" in src


def test_empty_dim_deep_search_switches_channels() -> None:
    src = inspect.getsource(pair_dimensions.deep_search_empty_dims)
    assert "min_similarity=0.2" not in src
    assert "DimensionRetrieveSpec" in src
    assert '("entity", "lexical")' in src.replace(" ", "") or '("entity","lexical")' in src.replace(" ", "")
