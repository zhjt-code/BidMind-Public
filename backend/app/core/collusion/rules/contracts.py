"""
职责: 定义规则注册表与事实词表的数据契约。
不做: 加载数据、执行检测、风险判定。
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.core.collusion.dimension_keys import COLLUSION_DIM_KEYS_SET

# ── 枚举字面量类型 ───────────────────────────────────────────────────────────
EvidenceLevel = Literal["first", "second", "third"]
DetectionMethod = Literal[
    "identifier_join", "text_residue_scan", "canonical_error",
    "shared_fingerprint", "asset_hash", "numeric_pattern",
    "metadata_join", "structure_fingerprint", "semantic_text",
    "response_pattern", "absence_pattern", "disclosure_relation", "manual",
]
Feasibility = Literal["exact", "computed", "llm_assisted", "manual"]
Priority = Literal["P0", "P1", "P2"]
LedgerStatus = Literal["checkable", "not_checkable", "unimplemented"]

# 内部校验集合（避免运行时用 get_args 解析 Literal）
_EVIDENCE_LEVELS: frozenset[str] = frozenset({"first", "second", "third"})
_DETECTION_METHODS: frozenset[str] = frozenset({
    "identifier_join", "text_residue_scan", "canonical_error", "shared_fingerprint",
    "asset_hash", "numeric_pattern", "metadata_join", "structure_fingerprint",
    "semantic_text", "response_pattern", "absence_pattern", "disclosure_relation", "manual",
})
_FEASIBILITIES: frozenset[str] = frozenset({"exact", "computed", "llm_assisted", "manual"})
_PRIORITIES: frozenset[str] = frozenset({"P0", "P1", "P2"})
_RULE_ID_RE = re.compile(r"^D[1-8]-\d{3}$")


@dataclass(frozen=True)
class FactField:
    """单个事实字段的元数据（名称、中文标签、现网可采集性）。"""

    name: str
    label: str
    collectable: bool


# 可采集事实规格（name, label），共 23 项
_COLLECTABLE: tuple[tuple[str, str], ...] = (
    ("company_name", "企业名称"),
    ("legal_representative", "法定代表人"),
    ("unified_social_credit_code", "统一社会信用代码"),
    ("contact_phone", "联系电话"),
    ("registered_address", "注册地址"),
    ("bank_account", "银行账户"),
    ("personnel_names", "人员姓名"),
    ("personnel_roles", "人员岗位"),
    ("personnel_certs", "人员证书编号"),
    ("personnel_id_tails", "人员身份证尾号"),
    ("total_price", "投标总价"),
    ("qualifications", "资质证书"),
    ("past_projects", "类似业绩"),
    ("subcontractors", "分包商与供应商"),
    ("technical_highlights", "技术方案要点"),
    ("text_chunks", "文档正文分块"),
    ("docx_structure", "文档结构指纹"),
    ("pdf_author", "PDF作者"),
    ("pdf_creator_tool", "PDF生成工具"),
    ("pdf_producer", "PDF输出程序"),
    ("pdf_subject_keywords", "PDF主题与关键词"),
    ("pdf_creation_date", "PDF创建时间"),
    ("pdf_mod_date", "PDF修改时间"),
)

# 暂不可采集事实规格，共 38 项
_NON_COLLECTABLE: tuple[tuple[str, str], ...] = (
    ("authorized_rep_name", "授权代理人姓名"),
    ("authorized_rep_id_tail", "授权代理人身份证尾号"),
    ("authorized_rep_phone", "授权代理人电话"),
    ("contact_person_name", "投标联系人姓名"),
    ("contact_person_phone", "投标联系人电话"),
    ("contact_person_email", "投标联系人邮箱"),
    ("personnel_phones", "人员电话"),
    ("personnel_emails", "人员邮箱"),
    ("company_email", "企业邮箱"),
    ("company_fax", "企业传真"),
    ("company_website", "企业网址"),
    ("mailing_address", "邮寄地址"),
    ("invoice_info", "发票信息"),
    ("finance_contact", "财务联系人"),
    ("bank_name", "开户银行"),
    ("disclosed_parent_company", "披露的母公司"),
    ("disclosed_affiliates", "披露的关联企业"),
    ("org_chart", "组织结构图"),
    ("price_items", "分项报价明细"),
    ("price_rates", "费率与税率"),
    ("multi_lot_prices", "多包多标段报价"),
    ("guarantee_account", "保证金账户"),
    ("equipment_list", "设备资源清单"),
    ("performance_contacts", "业绩联系人"),
    ("asset_hashes", "图片与附件哈希"),
    ("signature_images", "签名图像"),
    ("seal_images", "印章图像"),
    ("scan_features", "扫描物理特征"),
    ("page_layout_metrics", "页面排版度量"),
    ("format_error_markers", "格式错误标记"),
    ("section_tree", "章节树"),
    ("attachment_inventory", "附件清单"),
    ("requirement_responses", "条款响应对照"),
    ("tender_canonical_facts", "招标基准事实"),
    ("xmp_metadata", "XMP元数据"),
    ("docx_core_properties", "Office核心属性"),
    ("embedded_resources", "内嵌资源"),
    ("internal_font_resources", "内部字体资源"),
)

FACT_FIELDS: dict[str, FactField] = {
    **{name: FactField(name, label, True) for name, label in _COLLECTABLE},
    **{name: FactField(name, label, False) for name, label in _NON_COLLECTABLE},
}
_FACT_NAMES: frozenset[str] = frozenset(FACT_FIELDS.keys())


def fact_label(name: str) -> str:
    """返回事实字段的中文标签；未知名返回原名。"""
    f = FACT_FIELDS.get(name)
    return f.label if f is not None else name


# required_facts 中单个列表元素的类型（必需 str 或 any-of 组 list[str]）
FactItem = str | list[str]


class PatternGroupSpec(BaseModel):
    """联合指纹中的单个规则匹配组（rule_ids 或 dimension+min_level，二选一）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    min_hits: int
    rule_ids: list[str] | None = None
    dimension: str | None = None
    min_level: str | None = None

    @model_validator(mode="after")
    def _chk_group_type(self) -> "PatternGroupSpec":
        has_ids = self.rule_ids is not None
        has_dim = self.dimension is not None
        if has_ids == has_dim:
            raise ValueError("rule_ids 与 dimension 必须且只能指定其中之一")
        if has_dim:
            if self.min_level is None:
                raise ValueError("指定 dimension 时 min_level 不能为空")
            if self.min_level not in _EVIDENCE_LEVELS:
                raise ValueError(f"min_level {self.min_level!r} 须为 first|second|third")
            if self.dimension not in COLLUSION_DIM_KEYS_SET:
                raise ValueError(f"dimension {self.dimension!r} 不在已知维度列表中")
        return self


class PatternSpec(BaseModel):
    """联合指纹模式（多规则组联合触发）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pattern_id: str
    name: str
    required_rule_groups: list[PatternGroupSpec]
    min_groups_hit: int
    conclusion_label: str


class RuleSpec(BaseModel):
    """单条串标规则规格（含全量字段级与跨字段校验）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str
    dimension: str
    name: str
    evidence_levels: list[str]
    default_level: str
    standalone_conclusive: bool
    detection_method: str
    feasibility: str
    template_exclusion_required: bool
    legal_reference: str | None
    required_facts: dict[str, list[FactItem]]
    fact_key_template: str
    cross_rules: list[str]
    priority: str
    ledger_gate: bool

    @field_validator("rule_id")
    @classmethod
    def _chk_rule_id(cls, v: str) -> str:
        if not _RULE_ID_RE.match(v):
            raise ValueError(f"rule_id 格式非法: {v!r}，须满足 ^D[1-8]-\\d{{3}}$")
        return v

    @field_validator("dimension")
    @classmethod
    def _chk_dimension(cls, v: str) -> str:
        if v not in COLLUSION_DIM_KEYS_SET:
            raise ValueError(f"dimension {v!r} 不在已知维度列表中")
        return v

    @field_validator("evidence_levels")
    @classmethod
    def _chk_evidence_levels(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("evidence_levels 不能为空")
        bad = [x for x in v if x not in _EVIDENCE_LEVELS]
        if bad:
            raise ValueError(f"evidence_levels 含非法值: {bad}")
        return v

    @field_validator("detection_method")
    @classmethod
    def _chk_detection_method(cls, v: str) -> str:
        if v not in _DETECTION_METHODS:
            raise ValueError(f"detection_method {v!r} 非法")
        return v

    @field_validator("feasibility")
    @classmethod
    def _chk_feasibility(cls, v: str) -> str:
        if v not in _FEASIBILITIES:
            raise ValueError(f"feasibility {v!r} 非法")
        return v

    @field_validator("priority")
    @classmethod
    def _chk_priority(cls, v: str) -> str:
        if v not in _PRIORITIES:
            raise ValueError(f"priority {v!r} 非法")
        return v

    @field_validator("fact_key_template")
    @classmethod
    def _chk_fkt(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("fact_key_template 不能为空")
        return v

    @field_validator("required_facts")
    @classmethod
    def _chk_required_facts(cls, v: dict[str, list[Any]]) -> dict[str, list[Any]]:
        if set(v.keys()) != {"a", "b"}:
            raise ValueError(f"required_facts 键必须恰为 'a' 与 'b'，实际: {set(v.keys())}")
        for side, items in v.items():
            for item in items:
                if isinstance(item, list):
                    if not item:
                        raise ValueError(f"required_facts[{side!r}] 含空 any-of 组")
                    bad = [f for f in item if f not in _FACT_NAMES]
                else:
                    bad = [item] if item not in _FACT_NAMES else []
                if bad:
                    raise ValueError(f"required_facts[{side!r}] 含未知事实名: {bad}")
        return v

    @model_validator(mode="after")
    def _chk_cross_constraints(self) -> "RuleSpec":
        # default_level 须在 evidence_levels 内
        if self.default_level not in self.evidence_levels:
            raise ValueError(
                f"default_level {self.default_level!r} 不在 evidence_levels {self.evidence_levels} 中"
            )
        # standalone_conclusive 约束：必须 default_level=='first' 且 legal_reference 非 null
        if self.standalone_conclusive:
            if self.default_level != "first":
                raise ValueError("standalone_conclusive=True 时 default_level 必须为 'first'")
            if self.legal_reference is None:
                raise ValueError("standalone_conclusive=True 时 legal_reference 不能为 null")
        # manual 双向约束：detection_method=='manual' 当且仅当 feasibility=='manual'
        is_manual_m = self.detection_method == "manual"
        is_manual_f = self.feasibility == "manual"
        if is_manual_m != is_manual_f:
            raise ValueError(
                f"detection_method='manual' 当且仅当 feasibility='manual'，"
                f"当前: {self.detection_method!r}/{self.feasibility!r}"
            )
        return self

    def iter_required_fact_names(self, side: str) -> Iterator[str]:
        """展开指定侧所有事实名（含 any-of 组内元素），去重。"""
        seen: set[str] = set()
        for item in self.required_facts.get(side, []):
            names: list[str] = [item] if isinstance(item, str) else list(item)
            for name in names:
                if name not in seen:
                    seen.add(name)
                    yield name

    def missing_fact_groups(self, present: frozenset[str], side: str) -> list[list[str]]:
        """返回未被满足的事实组列表；单必需事实也包成单元素 list。

        any-of 组中至少有一个事实存在即视为该组已满足。
        """
        result: list[list[str]] = []
        for item in self.required_facts.get(side, []):
            if isinstance(item, str):
                if item not in present:
                    result.append([item])
            else:
                if not any(f in present for f in item):
                    result.append(list(item))
        return result
