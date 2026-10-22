"""评分 verdict 探针落库。

职责: 把 build_verdict_probe 产物写入 documents.scoring_verdict_probe, 并标记流程。
不做: 不提取、不聚合、不判绿、不提交事务(由调用方 session 负责)。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import update

from app.models.document import Document


async def stamp_document_verdict_probe(
    db: Any,
    document_id: int,
    probe: dict[str, Any] | None,
    *,
    flow: str,
) -> None:
    """审查/写标共用落库入口。probe 缺失则跳过, 避免空写覆盖已有样本。"""
    if not isinstance(probe, dict):
        return
    payload = dict(probe)
    payload["flow"] = flow
    await db.execute(
        update(Document)
        .where(Document.id == document_id)
        .values(scoring_verdict_probe=payload)
    )


__all__ = ["stamp_document_verdict_probe"]
