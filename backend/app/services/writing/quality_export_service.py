"""Writer quality regression export service.

职责: 组装无原文写标质量 corpus，供离线回归使用。
不做: 不触发一致性重算、不调用 LLM、不导出原文或素材正文。
"""

from __future__ import annotations

import hashlib
import json
from typing import Sequence

from app.core.writer.quality.contracts import (
    WriterQualityCorpusV1,
    WriterQualitySource,
)
from app.core.writer.quality.privacy import validate_private_export
from app.core.writer.quality.snapshot_builder import build_writer_quality_snapshot


class WriterQualityExportService:
    """Build no-source-text writer quality regression assets."""

    def __init__(self, db: object) -> None:
        self.db = db

    def build_anonymous_corpus_from_sources(
        self,
        sources: Sequence[WriterQualitySource],
        *,
        secret: bytes,
    ) -> WriterQualityCorpusV1:
        """Build a committed-safe corpus from preloaded source bundles."""

        if not sources:
            raise ValueError("no writer quality sources")
        if not secret:
            raise ValueError("secret must not be empty")

        snapshots = [
            build_writer_quality_snapshot(source)
            for source in sources
        ]
        digest_inputs = [snapshot.sample_set_digest for snapshot in snapshots]
        sample_set_digest = (
            digest_inputs[0]
            if len(set(digest_inputs)) == 1
            else hashlib.sha256(
                json.dumps(sorted(digest_inputs), separators=(",", ":")).encode("utf-8")
            ).hexdigest()
        )
        corpus = WriterQualityCorpusV1(
            sample_set_digest=sample_set_digest,
            snapshots=snapshots,
            blind_review_items=[],
        )
        validate_private_export(corpus.model_dump(mode="json"))
        return corpus


__all__ = ["WriterQualityExportService"]
