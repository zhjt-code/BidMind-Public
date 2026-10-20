"""评分结构判读契约(SchemeMap Pydantic 模型)。

职责: 定义判读器 LLM 输出的强类型结构(方案/卷基准/行区间归属/锚点/置信度)。
不做: 不做锚点核验(anchor_verifier 职责)、不做贴标(attribution 职责)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

VolumeKey = Literal[
    "technical", "commercial", "price", "qualification", "other", "unspecified"
]

AnchorKind = Literal["table_row", "heading", "declaration"]


class SignalAnchor(BaseModel):
    """边界判定的证据锚点: 必须能回到信号快照做 exact-match 核验。"""

    kind: AnchorKind
    page: int = Field(ge=1)
    table_index: int | None = Field(default=None, ge=0)
    row_index: int | None = Field(default=None, ge=0)
    quote: str = Field(min_length=1, max_length=200, description="信号快照中的逐字引用")


class SchemeVolumeDecl(BaseModel):
    """方案内单卷声明(卷别 + 原文卷名 + 基准分)。"""

    volume: VolumeKey
    label: str = Field(default="", max_length=60, description="卷/分组原文名")
    max_score: float | None = Field(default=None, ge=0, description="卷基准分, 未声明为 null")
    anchor: SignalAnchor | None = None


class SchemeDecl(BaseModel):
    """评分方案声明(一套独立的评分办法)。"""

    scheme_index: int = Field(ge=1)
    title: str = Field(default="", max_length=80, description="方案标题原文, 如 5.2.1 商务评审")
    total_score: float | None = Field(default=None, ge=0)
    volumes: list[SchemeVolumeDecl] = Field(default_factory=list)


class SpanAssignment(BaseModel):
    """行区间归属: 某表的 [row_start, row_end] 行属于哪个方案哪个卷。"""

    page: int = Field(ge=1)
    table_index: int = Field(ge=0)
    row_start: int = Field(ge=0)
    row_end: int = Field(ge=0, description="闭区间末行")
    scheme_index: int = Field(ge=1)
    volume: VolumeKey
    volume_label: str = Field(default="", max_length=60)
    boundary_anchor: SignalAnchor | None = Field(
        default=None, description="支持本归属的边界信号锚点(卷头行/合计行/标题)"
    )


class SchemeMap(BaseModel):
    """判读产物: 文档评分结构地图。"""

    schemes: list[SchemeDecl] = Field(default_factory=list)
    assignments: list[SpanAssignment] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    notes: str = Field(default="", max_length=500)


@dataclass
class SchemeMapReading:
    """判读结果(含锚点核验后的有效产物与诊断)。

 ok=False 时 verified_map 为 None, 消费方必须回退现行管线(fail-open)。
 verified_volume_keys: 卷声明锚点 exact-match 通过的 (scheme_index, volume) 集合;
 基准注入等高影响消费只准采纳其中的卷(: 未锚定声明是 claim 不是 fact)。
 """

    ok: bool
    raw_map: SchemeMap | None = None
    verified_map: SchemeMap | None = None
    effective_confidence: float = 0.0
    rejected_assignments: int = 0
    diagnostics: list[str] = field(default_factory=list)
    verified_volume_keys: set[tuple[int, str]] = field(default_factory=set)


__all__ = [
    "AnchorKind",
    "SchemeDecl",
    "SchemeMap",
    "SchemeMapReading",
    "SchemeVolumeDecl",
    "SignalAnchor",
    "SpanAssignment",
    "VolumeKey",
]
