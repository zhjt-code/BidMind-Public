"""标书素材匹配 -- 数据契约。

职责: 定义素材->章节匹配管线各阶段的数据模型(语义命题/类型门禁/召回候选/
 句对蕴含裁决/绑定提案)，作为管线内部传递与 LLM 结构化输出的统一契约。
不做: 不计算相似度、不查询数据库、不调用 LLM、不处理 HTTP 请求。

背景:
- 旧素材匹配以"多路召回 + Top-N 直接绑定"生成业务边，通用证明的高余弦近邻被
 当作有效绑定。新管线要求"完整语义命题"作为比对与裁决的基本单位，故此处的
 Profile 携带 proposition_text/demand_text 供向量召回与 LLM 裁决共同消费。
- 句对裁决关系 relation 与门禁 decision 使用 Literal 收敛取值域，并对 LLM 输出
 做 before 归一化，避免大小写/中文变体导致整批 Pydantic 校验失败。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

GateDecision = Literal["allow", "review", "block"]
PairRelation = Literal["supports", "partial", "unrelated", "conflict"]

# LLM 中文/变体关系词 -> 规范英文关系。未知值保守归为 unrelated(不放行)。
_RELATION_ALIASES: dict[str, str] = {
    "supports": "supports",
    "support": "supports",
    "支持": "supports",
    "相关": "supports",
    "匹配": "supports",
    "partial": "partial",
    "部分": "partial",
    "部分相关": "partial",
    "弱相关": "partial",
    "unrelated": "unrelated",
    "无关": "unrelated",
    "不相关": "unrelated",
    "conflict": "conflict",
    "冲突": "conflict",
    "矛盾": "conflict",
}


def make_pair_id(material_id: int, node_id: int) -> str:
    """生成素材-章节候选对的稳定 ID，召回与裁决使用同一口径。"""
    return f"m{material_id}_n{node_id}"


def parse_pair_id(pair_id: str) -> Optional[tuple[int, int]]:
    """从 pair_id 解析 (material_id, node_id)，格式非法时返回 None。"""
    if not pair_id or not pair_id.startswith("m") or "_n" not in pair_id:
        return None
    try:
        material_part, node_part = pair_id[1:].split("_n", 1)
        return int(material_part), int(node_part)
    except (ValueError, IndexError):
        return None


class MaterialSemanticProfile(BaseModel):
    """素材语义命题: 以业务类型/用途/领域/实体组成的完整命题，用于门禁与召回。"""

    material_id: int = Field(..., description="素材ID")
    title: str = Field(default="", description="素材有效标题")
    material_type: str = Field(default="", description="素材物理类型(text/image/table/...)")
    resolved_label: Optional[str] = Field(default=None, description="决议后的业务细标签(唯一真源)")
    business_type: Optional[str] = Field(default=None, description="DocumentBusinessType 大类值")
    domain: str = Field(default="", description="LLM 打标领域")
    bid_purpose: str = Field(default="", description="素材在投标中的业务用途")
    entities: List[str] = Field(default_factory=list, description="关键实体")
    applicable_sections: List[str] = Field(default_factory=list, description="LLM 判定的适用章节")
    inapplicable_sections: List[str] = Field(default_factory=list, description="明确不适用章节")
    exclusive_section: Optional[str] = Field(default=None, description="排他性唯一章节")
    label_verified: bool = Field(
        default=True,
        description="标签是否经校验(图片素材需 label_conflict==matched)，未验证图片禁止精确/语义放行",
    )
    proposition_text: str = Field(default="", description="用于语义召回编码的完整命题文本")
    profile_complete: bool = Field(
        default=False,
        description="画像是否完整(有类型真源+用途/领域)，不完整时禁止语义单路放行",
    )


class ChapterDemandProfile(BaseModel):
    """章节需求命题: 由标题/功能定位/期望/排除组成，描述章节真正需要的内容。"""

    node_id: int = Field(..., description="目录节点ID")
    title: str = Field(default="", description="章节标题")
    node_type: str = Field(default="", description="节点类型(fixed_template/data_fill/free_writing)")
    volume_type: Optional[str] = Field(default=None, description="所属分卷(technical/commercial)")
    semantic_role: str = Field(default="", description="章节功能定位")
    expects: List[str] = Field(default_factory=list, description="期望出现的内容类型")
    rejects: List[str] = Field(default_factory=list, description="明确不应出现的内容类型")
    demand_text: str = Field(default="", description="用于语义召回编码的章节需求文本")
    profile_complete: bool = Field(
        default=False,
        description="画像是否完整(SSOT matchable: 有功能定位且期望非空)；degraded 时禁止纯语义单路",
    )


class PairGateResult(BaseModel):
    """类型硬门禁裁决结果。"""

    decision: GateDecision = Field(..., description="allow=类型一致/review=信息不足/block=类型冲突")
    reason: str = Field(default="", description="门禁裁决依据")


class MaterialChapterCandidate(BaseModel):
    """通过门禁与召回后进入 LLM 裁决的素材-章节候选对。"""

    material_id: int = Field(..., description="素材ID")
    node_id: int = Field(..., description="章节节点ID")
    pair_id: str = Field(default="", description="候选对稳定ID")
    recall_score: float = Field(default=0.0, description="综合召回分(标题/标签/语义)")
    recall_sources: List[str] = Field(default_factory=list, description="召回来源标记")
    gate_decision: Literal["allow", "review"] = Field(
        default="review", description="门禁裁决(block 的候选不会进入本集合)"
    )
    gate_reason: str = Field(default="", description="门禁裁决依据")

    @model_validator(mode="after")
    def _ensure_pair_id(self) -> "MaterialChapterCandidate":
        if not self.pair_id:
            self.pair_id = make_pair_id(self.material_id, self.node_id)
        return self


class MaterialPairDecision(BaseModel):
    """LLM 对单个素材-章节候选对的蕴含裁决。"""

    pair_id: str = Field(..., description="候选对稳定ID")
    relation: PairRelation = Field(..., description="supports/partial/unrelated/conflict")
    confidence: float = Field(default=0.0, description="裁决置信度 0.0~1.0")
    evidence_quote: str = Field(default="", description="支撑裁决的证据摘录")
    reason: str = Field(default="", description="一句话裁决理由")

    @field_validator("relation", mode="before")
    @classmethod
    def _normalize_relation(cls, value: Any) -> str:
        key = str(value or "").strip().lower()
        return _RELATION_ALIASES.get(key, "unrelated")

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, value: Any) -> float:
        try:
            score = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, score))


class MaterialPairJudgeBatch(BaseModel):
    """LLM 句对裁决批量输出(material_pair_judge 节点契约)。"""

    decisions: List[MaterialPairDecision] = Field(
        default_factory=list, description="逐候选对的裁决列表"
    )


class MaterialBindingProposal(BaseModel):
    """管线最终产出的素材->章节绑定提案，由 matcher 转为 raw binding 落库。"""

    material_id: int = Field(..., description="素材ID")
    outline_node_id: Optional[int] = Field(
        ..., description="目标章节节点ID；None 表示未应用素材的留痕记录"
    )
    confidence: float = Field(default=0.0, description="绑定置信度")
    reason: str = Field(default="", description="绑定理由")
    source: str = Field(default="auto", description="来源标记")
    needs_review: bool = Field(
        default=False, description="是否为需人工重点复核的弱绑定(partial)"
    )
    scope: Optional[str] = Field(default=None, description="绑定作用域/留痕类型")


@dataclass(slots=True)
class MaterialMatchingRequest:
    """素材匹配管线输入。

    职责: 承载一次匹配所需的已加载 ORM 实体与 Phase 2 的约束->章节中间边，
          避免管线内部再回查数据库。
    """

    project_id: int
    materials: list = field(default_factory=list)
    nodes: list = field(default_factory=list)
    clauses: list = field(default_factory=list)
    c2n_bindings: list = field(default_factory=list)


__all__ = [
    "GateDecision",
    "PairRelation",
    "make_pair_id",
    "parse_pair_id",
    "MaterialSemanticProfile",
    "ChapterDemandProfile",
    "PairGateResult",
    "MaterialChapterCandidate",
    "MaterialPairDecision",
    "MaterialPairJudgeBatch",
    "MaterialBindingProposal",
    "MaterialMatchingRequest",
]
