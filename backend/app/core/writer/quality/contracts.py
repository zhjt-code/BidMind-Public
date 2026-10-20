"""Writer quality regression data contracts.

职责: 定义不含原文的写标质量快照契约。
不做: 不读取数据库、不脱敏原文、不执行质量评审。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Sequence

from pydantic import BaseModel, ConfigDict, Field


class StrictQualityModel(BaseModel):
    """Base model with fail-closed extra-field handling."""

    model_config = ConfigDict(extra="forbid")


class SignalAvailability(str, Enum):
    """Availability of a quality signal in historical writer data."""

    observed = "observed"
    partial_nonpass_only = "partial_nonpass_only"
    unavailable = "unavailable"


class BlindReviewItemV1(StrictQualityModel):
    """Private blind-review item metadata.

    The actual anonymized review text must stay in scratch/private exports and
    is intentionally not represented here.
    """

    item_id: str = Field(..., min_length=3, max_length=80)
    project_ref: str = Field(..., min_length=3, max_length=80)
    node_ref: str = Field(..., min_length=3, max_length=80)
    node_type: str = Field(..., min_length=1, max_length=40)
    word_count_bucket: str = Field(default="unknown", max_length=40)


class WriterQualitySnapshotV1(StrictQualityModel):
    """No-source-text writer quality snapshot."""

    schema_version: str = "writer_quality_snapshot.v1"
    sample_set_digest: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    project_count: int = Field(..., ge=0)
    section_count: int = Field(..., ge=0)
    placeholder_count: int = Field(..., ge=0)
    high_issue_count: int = Field(..., ge=0)
    fixed_template_high_issue_count: int = Field(..., ge=0)
    drafts_without_material_sources: int = Field(..., ge=0)
    failed_drafts: int = Field(..., ge=0)
    needs_review_drafts: int = Field(..., ge=0)
    gate_signal_availability: SignalAvailability
    judge_signal_availability: SignalAvailability
    cove_signal_availability: SignalAvailability

    def canonical_payload(self) -> dict[str, object]:
        """Return a stable JSON-serializable payload for hashing."""

        return self.model_dump(mode="json")

    def canonical_digest(self) -> str:
        """Return SHA-256 over canonical JSON representation."""

        raw = json.dumps(
            self.canonical_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class WriterQualityCorpusV1(StrictQualityModel):
    """Small anonymized corpus metadata committed to regression fixtures."""

    schema_version: str = "writer_quality_corpus.v1"
    sample_set_digest: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    snapshots: list[WriterQualitySnapshotV1] = Field(default_factory=list)
    blind_review_items: list[BlindReviewItemV1] = Field(default_factory=list)


@dataclass(frozen=True)
class WriterQualitySource:
    """In-memory source bundle for no-source-text quality snapshot building."""

    sample_set_digest: str
    project_ids: tuple[int, ...]
    nodes: Sequence[Any]
    drafts: Sequence[Any]
    consistency_issues: Sequence[dict[str, Any]]
    bindings: Sequence[Any]
    industry_profile_clause_ids: Sequence[int]
    judge_signal_availability: SignalAvailability
    cove_signal_availability: SignalAvailability


__all__ = [
    "BlindReviewItemV1",
    "SignalAvailability",
    "WriterQualityCorpusV1",
    "WriterQualitySource",
    "WriterQualitySnapshotV1",
]
