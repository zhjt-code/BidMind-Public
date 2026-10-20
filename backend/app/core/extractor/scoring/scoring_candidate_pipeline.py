"""评分chunk候选解析管线。

职责: 从scoring_table chunk读取结构化载荷并调用现有确定性解析器生成候选结果。
不做: 不负责PDF表格装配、不做去重/落库/LLM修补、不改变评分项事实集合。
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any

from app.core.extractor.scoring.scoring_grid_payload import (
    grid_from_payload,
    payload_from_chunk,
)
from app.core.extractor.scoring.scoring_table_parser import ParsedScoringTable, parse_scoring_table


@dataclass(frozen=True)
class ScoringChunkCandidate:
    """单个评分chunk的确定性解析候选。"""

    parsed: ParsedScoringTable
    payload: dict[str, Any] | None
    fingerprint: str


def parse_scoring_chunk_candidate(chunk: Any) -> ScoringChunkCandidate:
    """解析单个评分chunk并保留载荷上下文。"""
    payload = payload_from_chunk(chunk)
    parsed = parse_scoring_table(
        str(getattr(chunk, "content", "") or ""),
        section_path=str(getattr(chunk, "section_path", "") or ""),
        grid=grid_from_payload(payload),
        column_roles=(payload or {}).get("column_roles"),
        scope_rows=(payload or {}).get("scope_rows"),
    )
    # 合并 payload.parse_diagnostics 到 parsed.diagnostics，供下游审计/needs_review 链使用
    parse_diags: list[str] = (payload or {}).get("parse_diagnostics") or []
    if parse_diags:
        parsed = dataclasses.replace(
            parsed, diagnostics=list(parsed.diagnostics) + [str(d) for d in parse_diags]
        )
    return ScoringChunkCandidate(
        parsed=parsed,
        payload=payload,
        fingerprint=str((payload or {}).get("table_fingerprint") or ""),
    )


__all__ = ["ScoringChunkCandidate", "parse_scoring_chunk_candidate"]
