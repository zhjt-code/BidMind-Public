"""完整候选输出契约——EvidenceRef / Fact / ScoringBasis / Scope / Request。

职责: 承载 L3 完整候选提取的不可变输出契约（Pydantic v2）和请求容器 dataclass；
      所有 Pydantic 层 extra=forbid，构造时复制可变容器。
不做: 不执行提取逻辑、不调用 LLM、不访问数据库、不修改 scope_validator 或 repair 模块。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.extractor.scoring.scoring_scope_contracts import (
    ScopeIdentity,
    ScoringScopeExpectation,
)

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

SupportedField = Literal[
    "category",
    "score_item_name",
    "description",
    "score_weight",
    "fact_type",
    "volume",
    "volume_label",
    "score_rules",
    "deduction_rules",
]

_SENTINEL: object = object()

# 模型会把系统上下文里的作用域/卷元数据冗余回显进 fact——这些键由系统
# 权威声明、fact 层不消费, 剥离不构成信息采纳; 其余未知键仍走 extra=forbid 拒绝。
_FACT_REDUNDANT_CONTEXT_KEYS: tuple[str, ...] = (
    "lot",
    "scheme",
    "volume_weight",
    "volume_max",
    "source_pages",
    "supports_fields",
    "shared",
)

# 约束解码不可用时(如 qwen-max 自由生成), 模型会把 evidence_refs 内层结构
# 平铺到 fact 顶层——这三个键齐备时可无损提升回 evidence_refs。
_FACT_FLATTENED_EVIDENCE_KEYS: tuple[str, ...] = (
    "source_chunk_id",
    "source_page",
    "evidence_quote",
)


def _synth_supports_fields(data: dict) -> list[str]:
    """为提升出的证据引用合成 supports_fields 声明(基础必需字段 + fact 实有的可选字段)。

    仅是"声明"——下游 validate_candidate 仍按 chunk 原文硬锚定复验
    (quote 逐字命中/页范围/name 逐字/score 在 quote), 声明不实必被拒绝。
    """
    fields: list[str] = [
        "category", "score_item_name", "description",
        "score_weight", "fact_type", "volume",
    ]
    if data.get("volume_label"):
        fields.append("volume_label")
    if data.get("score_rules"):
        fields.append("score_rules")
    if data.get("deduction_rules"):
        fields.append("deduction_rules")
    return fields


def _deserialize_stringified_json(value: object) -> object:
    """: 归一"嵌套结构被模型 stringify 成 JSON 字符串"的形变。

 仅当字符串以 [ 或 { 开头且 json.loads 成功时替换为解析结果;
 解析失败或非容器内容保持原值(fail-visible, 交由后续校验拒绝)。
 """
    import json

    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{":
        return value
    try:
        parsed = json.loads(stripped)
    except (ValueError, TypeError):
        return value
    return parsed if isinstance(parsed, (list, dict)) else value


def _lift_flattened_evidence(data: dict) -> dict:
    """: 归一"证据字段平铺到 fact 顶层"的模型形变(原地修改并返回 data)。

 形变A: evidence_refs 已是合法非空数组 → 顶层平铺键属冗余回显, 剥离(同语义);
 形变B: evidence_refs 缺失/非法(如垃圾字符串)且三个平铺键齐备且类型正确
 → 提升为单条 evidence_ref, supports_fields 由 _synth_supports_fields 合成;
 其余形态(平铺键不齐/类型错误)不归一, 保持 extra=forbid fail-visible。
 """
    # 字符串化 JSON 先反序列化(2306 实测 qwen-max 输出内容完整但被 stringify)
    if isinstance(data.get("evidence_refs"), str):
        data["evidence_refs"] = _deserialize_stringified_json(data["evidence_refs"])
    refs = data.get("evidence_refs")
    if isinstance(refs, (list, tuple)) and refs:
        for key in _FACT_FLATTENED_EVIDENCE_KEYS:
            data.pop(key, None)
        return data

    if not all(key in data for key in _FACT_FLATTENED_EVIDENCE_KEYS):
        return data
    chunk_id = data.get("source_chunk_id")
    page = data.get("source_page")
    quote = data.get("evidence_quote")
    if isinstance(chunk_id, bool) or not isinstance(chunk_id, int):
        return data
    if isinstance(page, bool) or not isinstance(page, int):
        return data
    if not isinstance(quote, str) or not quote.strip():
        return data

    for key in _FACT_FLATTENED_EVIDENCE_KEYS:
        data.pop(key)
    data.pop("evidence_refs", None)  # 垃圾串等非法形态一并清除
    data["evidence_refs"] = [{
        "source_chunk_id": chunk_id,
        "source_page": page,
        "evidence_quote": quote,
        "supports_fields": _synth_supports_fields(data),
    }]
    return data


# ---------------------------------------------------------------------------
# FullCandidateEvidenceRef
# ---------------------------------------------------------------------------


class FullCandidateEvidenceRef(BaseModel):
    """单条证据引用，声明其所支持的字段集合。

    职责: 承载单条 chunk 引用及该引用证明的字段列表；冻结不可变，extra 严格拒绝。
    不做: 不执行任何逻辑，仅为数据契约。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_chunk_id: int = Field(gt=0, description="来源 chunk ID，必须 >0")
    source_page: int = Field(gt=0, description="来源页码，必须 >0")
    evidence_quote: str = Field(description="原文引用，非空")
    supports_fields: tuple[SupportedField, ...] = Field(
        description="此引用所支持的字段，至少一项，不重复"
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_known_shapes(cls, data: Any) -> Any:
        # 真实模型常把单元素数组降维成字符串, 归一为单元素容器。
        if isinstance(data, dict) and isinstance(data.get("supports_fields"), str):
            data = dict(data)
            data["supports_fields"] = [data["supports_fields"]]
        return data

    @field_validator("evidence_quote")
    @classmethod
    def _quote_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("evidence_quote 不得为空或纯空白")
        return v

    @field_validator("supports_fields")
    @classmethod
    def _fields_nonempty_unique(cls, v: tuple) -> tuple:
        if not v:
            raise ValueError("supports_fields 至少须有一项")
        if len(v) != len(set(v)):
            raise ValueError(f"supports_fields 不得重复，实际: {list(v)}")
        return v


# ---------------------------------------------------------------------------
# FullCandidateFact
# ---------------------------------------------------------------------------


class FullCandidateFact(BaseModel):
    """单条完整候选评分事实，含多条证据引用。

    职责: 承载评分项的结构化信息与多证据引用，提供只读派生属性。
    不做: 不执行评分计算，不访问外部资源。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: str
    score_item_name: str
    description: str
    score_weight: float
    fact_type: str
    volume: str
    volume_label: str
    score_rules: tuple[str, ...]
    deduction_rules: tuple[str, ...]
    evidence_refs: tuple[FullCandidateEvidenceRef, ...]

    @model_validator(mode="before")
    @classmethod
    def _normalize_known_shapes(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for key in ("score_rules", "deduction_rules"):
            value = data.get(key)
            if isinstance(value, str):
                data[key] = [value] if value.strip() else []
        # 平铺证据归一必须先于冗余键剥离——形变B 需要读取平铺键原值
        data = _lift_flattened_evidence(data)
        for key in _FACT_REDUNDANT_CONTEXT_KEYS:
            data.pop(key, None)
        return data

    @field_validator("evidence_refs")
    @classmethod
    def _refs_nonempty(cls, v: tuple) -> tuple:
        if not v:
            raise ValueError("evidence_refs 至少须有一条证据引用")
        return v

    @property
    def source_pages(self) -> tuple[int, ...]:
        """去重有序的来源页码列表。"""
        seen: set[int] = set()
        result: list[int] = []
        for ref in self.evidence_refs:
            if ref.source_page not in seen:
                seen.add(ref.source_page)
                result.append(ref.source_page)
        return tuple(result)

    @property
    def source_chunk_ids(self) -> tuple[int, ...]:
        """去重有序的来源 chunk ID 列表。"""
        seen: set[int] = set()
        result: list[int] = []
        for ref in self.evidence_refs:
            if ref.source_chunk_id not in seen:
                seen.add(ref.source_chunk_id)
                result.append(ref.source_chunk_id)
        return tuple(result)

    @property
    def evidence_quote(self) -> str:
        """兼容摘要：返回第一条引用原文。不得替代 evidence_refs 的逐字段证明。"""
        return self.evidence_refs[0].evidence_quote if self.evidence_refs else ""


# ---------------------------------------------------------------------------
# FullCandidateScoringBasis
# ---------------------------------------------------------------------------


class FullCandidateScoringBasis(BaseModel):
    """评分基准，与 ScoringScopeExpectation 对齐。

    职责: 承载 scope 期望总分或分卷期望；至少一者有效，值均须 >0，volume key 非空不重复。
    不做: 不执行校验逻辑，不从已提取结果反推期望。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_total: float | None = None
    expected_by_volume: tuple[tuple[str, float], ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _normalize_known_shapes(cls, data: Any) -> Any:
        """: 归一真实模型的三种已知形变, 未知非数值键仍拒绝。

 1. expected_by_volume 为字典 {"technical": 57.0} -> 对元组;
 2. expected_by_volume 为平铺单对 ["technical", 57.0] -> 包装一层;
 3. 卷分平铺成顶层键 {"price": 30.0} -> 折入 expected_by_volume(不覆盖已有卷)。
 """
        if not isinstance(data, dict):
            return data
        data = dict(data)
        ebv = data.get("expected_by_volume")
        if isinstance(ebv, dict):
            data["expected_by_volume"] = [[str(k), v] for k, v in ebv.items()]
        elif (
            isinstance(ebv, (list, tuple))
            and len(ebv) == 2
            and isinstance(ebv[0], str)
            and isinstance(ebv[1], (int, float))
            and not isinstance(ebv[1], bool)
        ):
            data["expected_by_volume"] = [list(ebv)]
        flat_pairs = [
            (key, value)
            for key, value in data.items()
            if key not in ("expected_total", "expected_by_volume")
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        ]
        if flat_pairs:
            merged = [list(pair) for pair in (data.get("expected_by_volume") or [])]
            seen_keys = {
                pair[0]
                for pair in merged
                if isinstance(pair, (list, tuple)) and pair
            }
            for key, value in flat_pairs:
                data.pop(key)
                if key not in seen_keys:
                    merged.append([key, value])
            data["expected_by_volume"] = merged
        return data

    @model_validator(mode="after")
    def _at_least_one_valid(self) -> "FullCandidateScoringBasis":
        if self.expected_total is None and not self.expected_by_volume:
            raise ValueError(
                "expected_total 与 expected_by_volume 至少须有一者有效"
            )
        if self.expected_total is not None and self.expected_total <= 0:
            raise ValueError(f"expected_total 必须 >0，实际: {self.expected_total}")
        if self.expected_by_volume:
            keys = [k for k, _ in self.expected_by_volume]
            if len(keys) != len(set(keys)):
                raise ValueError(f"expected_by_volume volume key 不得重复: {keys}")
            for k, v in self.expected_by_volume:
                if not k:
                    raise ValueError("expected_by_volume volume key 不得为空")
                if v <= 0:
                    raise ValueError(
                        f"expected_by_volume 期望值必须 >0，违规值: {v}"
                    )
        return self

    @classmethod
    def from_scope_expectation(
        cls, exp: ScoringScopeExpectation
    ) -> "FullCandidateScoringBasis":
        """从权威 ScoringScopeExpectation 创建基准。"""
        return cls(
            expected_total=exp.expected_total,
            expected_by_volume=exp.expected_by_volume,
        )


# ---------------------------------------------------------------------------
# FullCandidateScope
# ---------------------------------------------------------------------------


class FullCandidateScope(BaseModel):
    """单个 scheme×lot 的完整候选提取结果。

    职责: 承载 scope 标识、评分基准和事实列表；facts 允许为空（表示模型无法完成）。
    不做: 不执行校验逻辑，不访问外部资源。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    scheme: str
    lot: str
    scoring_basis: FullCandidateScoringBasis
    facts: tuple[FullCandidateFact, ...] = ()
    declared_total: float | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_known_shapes(cls, data: Any) -> Any:
        """: 剔除 facts 中的游离字符串(模型回显 schema 字段名)与冗余 shared 键。"""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        data.pop("shared", None)
        facts = data.get("facts")
        if isinstance(facts, (list, tuple)):
            data["facts"] = [
                fact for fact in facts
                if isinstance(fact, (dict, FullCandidateFact))
            ]
        return data


# ---------------------------------------------------------------------------
# FullCandidateRequest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FullCandidateRequest:
    """L3 完整候选提取请求容器（一次 request/bundle 只允许一个 scheme×lot）。

    职责: 承载单次提取所需的全部只读输入；构造时验证预算/令牌 >0 且 bundle scope 一致。
    不做: 不执行提取逻辑，不从当前总分反推期望，不修改任何外部状态。
    """

    target_scope: ScopeIdentity
    current_items: tuple
    volume_defs: tuple
    scope_expectation: ScoringScopeExpectation
    diagnostics: tuple
    evidence_bundle: Any  # ScoringScopeEvidenceBundle at runtime
    context_char_budget: int
    max_tokens: int

    def __post_init__(self) -> None:
        if self.context_char_budget <= 0:
            raise ValueError(
                f"context_char_budget 必须 >0，实际: {self.context_char_budget}"
            )
        if self.max_tokens <= 0:
            raise ValueError(f"max_tokens 必须 >0，实际: {self.max_tokens}")

        bundle_scope = getattr(self.evidence_bundle, "target_scope", _SENTINEL)
        if bundle_scope is _SENTINEL:
            raise ValueError("evidence_bundle 缺少 target_scope 属性")
        if bundle_scope != self.target_scope:
            raise ValueError(
                f"evidence_bundle.target_scope {bundle_scope!r} 与 "
                f"target_scope {self.target_scope!r} 不一致"
            )

        # 复制可变容器为 tuple，确保快照不可变
        if not isinstance(self.current_items, tuple):
            object.__setattr__(self, "current_items", tuple(self.current_items))
        if not isinstance(self.volume_defs, tuple):
            object.__setattr__(self, "volume_defs", tuple(self.volume_defs))
        if not isinstance(self.diagnostics, tuple):
            object.__setattr__(self, "diagnostics", tuple(self.diagnostics))


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------

__all__ = [
    "FullCandidateEvidenceRef",
    "FullCandidateFact",
    "FullCandidateRequest",
    "FullCandidateScope",
    "FullCandidateScoringBasis",
    "SupportedField",
]
