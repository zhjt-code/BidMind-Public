"""跨字段串联引擎

对项目内所有投标文件的 IdentifierIndex 进行 N×N 双层扫描，
识别跨字段、跨主体的标识符串联（业内称"关键信息串联"或
"作者一致性"），落地《招标投标法实施条例》第 40 条第 6 项。

核心场景：
- 文档A的PDF作者 = 文档B的法定代表人 / 公司名 / 项目人员
- A的法定代表人 = B的项目人员
- A的联系电话 = B的项目人员手机
- A的分包商 = B的公司名（投标方互为分包）
- A的银行账号字段 = B的PDF作者字段

设计原则：
- 同类字段命中（如双方都在 company_names）已由 GlobalFeatureAnalyzer
  的 _build_company_relations 处理，本模块不重复
- 误报抑制：短串/纯数字/常见停用名/招标方名称黑名单 → 跳过
- 每条命中输出 CrossFieldHit，含 risk_pattern + 法规对照
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import Final, Optional

from loguru import logger

from app.core.collusion.identifier_index import (
    FieldRef,
    IdentifierIndex,
)
from app.schemas.collusion import is_valid_person_name

# ── 误报抑制 ──

_STOPWORD_PERSONS: Final[frozenset[str]] = frozenset({
    "<PERSON_1>", "<PERSON_2>", "<PERSON_3>", "<PERSON_7>", "未知", "未提供", "无", "n/a", "na",
    "admin", "user", "test", "测试", "系统",
})

_STOPWORD_COMPANIES: Final[frozenset[str]] = frozenset({
    "无", "未知", "未提供", "n/a", "na", "公司", "有限公司",
})

# 标识符最小长度（中英文混合策略）
_MIN_LEN_PERSON = 2
_MIN_LEN_COMPANY = 4        # 纯英文/数字公司名最低长度
_MIN_LEN_COMPANY_CN = 2     # 中文公司核心词最低字数（如"中建"是合规的）
_MIN_LEN_GENERIC = 4        # 通用标识符（含 author 字段）最低长度
_MIN_LEN_GENERIC_CN = 2     # 含中文时通用标识符最低中文字数


def _count_chinese(s: str) -> int:
    return sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")


def _is_meaningful_value(value: str, kind: str) -> bool:
    """判断标识符值是否值得参与跨字段比对（避免误报）"""
    if not value:
        return False
    v = value.strip().lower()
    if not v:
        return False
    cn_chars = _count_chinese(v)
    if kind == "person":
        if v in _STOPWORD_PERSONS:
            return False
        if cn_chars >= _MIN_LEN_PERSON:
            return is_valid_person_name(v)
        # 非中文姓名按整体长度判定
        if len(v) < _MIN_LEN_PERSON:
            return False
        return is_valid_person_name(v)
    if kind == "company":
        if v in _STOPWORD_COMPANIES:
            return False
        if cn_chars >= _MIN_LEN_COMPANY_CN:
            return True
        return len(v) >= _MIN_LEN_COMPANY
    if kind == "phone":
        digits = "".join(ch for ch in v if ch.isdigit())
        return len(digits) >= 7
    if kind == "generic":
        if cn_chars >= _MIN_LEN_GENERIC_CN:
            return True
        return len(v) >= _MIN_LEN_GENERIC
    if cn_chars >= _MIN_LEN_GENERIC_CN:
        return True
    return len(v) >= _MIN_LEN_GENERIC


# ── 命中数据结构 ──

@dataclass
class CrossFieldHit:
    """单条跨字段命中"""
    pair_id: str               # "doc_a_id:doc_b_id"
    rule_id: str
    risk_pattern: str          # 业务级标签（如 author_eq_other_legal_rep）
    target_dim: str            # 归类的维度: D1/D2/D5/D8
    level: str                 # first / second / third
    certainty: float
    matched_value: str         # 命中的归一化值
    description: str           # 自然语言描述（含真实文件名 + 字段含义）

    left_doc_id: int
    left_doc_name: str
    left_field: str
    left_quote: str
    left_page: Optional[str]

    right_doc_id: int
    right_doc_name: str
    right_field: str
    right_quote: str
    right_page: Optional[str]

    legal_reference: Optional[str] = None  # 法规对照
    extra: dict = field(default_factory=dict)


# ── 规则表 ──

@dataclass
class CrossFieldRule:
    """跨字段命中规则定义"""
    rule_id: str
    risk_pattern: str
    left_attr: str             # IdentifierIndex 上的属性名
    right_attr: str
    target_dim: str
    level: str
    certainty: float
    description_template: str  # {left_doc}/{right_doc}/{left_field}/{right_field}/{value}
    legal_reference: Optional[str] = None
    value_kind: str = "generic"  # 用于 _is_meaningful_value
    symmetric: bool = False    # 若 True, 同一 pair 对 (a, b) 与 (b, a) 都扫描


_LEGAL_REF_LAW40_6 = "《招标投标法实施条例》第40条第6项（法人/项目管理成员/被授权人/联系人为同一人）"
_LEGAL_REF_FIN87_37 = "《政府采购货物和服务招标投标管理办法》（财政部令第87号）第三十七条"

_RULES: Final[list[CrossFieldRule]] = [
    # PDF作者 → 他方主体类
    CrossFieldRule(
        rule_id="R01",
        risk_pattern="author_eq_other_legal_rep",
        left_attr="pdf_authors",
        right_attr="legal_representatives",
        target_dim="D8",
        level="first",
        certainty=0.9,
        description_template=(
            "{left_doc}的PDF作者「{value}」与{right_doc}的法定代表人完全一致，"
            "疑似同一人代写多份投标文件"
        ),
        legal_reference=_LEGAL_REF_LAW40_6,
        value_kind="person",
        symmetric=True,
    ),
    CrossFieldRule(
        rule_id="R02",
        risk_pattern="author_eq_other_company",
        left_attr="pdf_authors",
        right_attr="company_names",
        target_dim="D8",
        level="second",
        certainty=0.7,
        description_template=(
            "{left_doc}的PDF作者「{value}」与{right_doc}的投标公司名称重合，"
            "疑似投标主体跨标关联"
        ),
        legal_reference=_LEGAL_REF_LAW40_6,
        value_kind="company",
        symmetric=True,
    ),
    CrossFieldRule(
        rule_id="R03",
        risk_pattern="author_eq_other_person",
        left_attr="pdf_authors",
        right_attr="person_names",
        target_dim="D8",
        level="second",
        certainty=0.7,
        description_template=(
            "{left_doc}的PDF作者「{value}」出现在{right_doc}的项目人员名单中，"
            "疑似同一人参与多份投标制作"
        ),
        legal_reference=_LEGAL_REF_LAW40_6,
        value_kind="person",
        symmetric=True,
    ),

    # PDF生成软件作者类（creator_tool 一般是 "Microsoft Word"，本规则用于 author 风格的非工具值）
    CrossFieldRule(
        rule_id="R04",
        risk_pattern="creator_eq_other_company",
        left_attr="pdf_creators",
        right_attr="company_names",
        target_dim="D8",
        level="third",
        certainty=0.5,
        description_template=(
            "{left_doc}的PDF生成软件信息「{value}」与{right_doc}的公司名称重合，"
            "建议复核"
        ),
        legal_reference=None,
        value_kind="generic",
        symmetric=True,
    ),

    # 主体身份串联（法规第40条第6项核心情形）
    CrossFieldRule(
        rule_id="R05",
        risk_pattern="legal_rep_eq_other_personnel",
        left_attr="legal_representatives",
        right_attr="person_names",
        target_dim="D2",
        level="first",
        certainty=0.85,
        description_template=(
            "{left_doc}的法定代表人「{value}」出现在{right_doc}的项目人员名单中，"
            "命中"
            + _LEGAL_REF_LAW40_6
        ),
        legal_reference=_LEGAL_REF_LAW40_6,
        value_kind="person",
        symmetric=True,
    ),

    # 通讯类
    CrossFieldRule(
        rule_id="R06",
        risk_pattern="phone_cross_field",
        left_attr="phones",
        right_attr="phones",
        target_dim="D2",
        level="second",
        certainty=0.7,
        description_template=(
            "{left_doc}的{left_field_label}「{value}」与{right_doc}的{right_field_label}一致"
        ),
        legal_reference=_LEGAL_REF_LAW40_6,
        value_kind="phone",
        symmetric=False,  # phones 内部需自定义"跨字段"判断（同字段不算）
    ),

    # 关联实体
    CrossFieldRule(
        rule_id="R07",
        risk_pattern="subcontractor_is_other_bidder",
        left_attr="subcontractors",
        right_attr="company_names",
        target_dim="D5",
        level="second",
        certainty=0.7,
        description_template=(
            "{left_doc}的分包商/供应商「{value}」即{right_doc}的投标主体，"
            "投标方互为分包，疑似围标"
        ),
        legal_reference=_LEGAL_REF_FIN87_37,
        value_kind="company",
        symmetric=True,
    ),

    # 银行账号字段 ↔ 元数据作者（少见但强信号）
    CrossFieldRule(
        rule_id="R08",
        risk_pattern="bank_eq_other_author",
        left_attr="bank_accounts",
        right_attr="pdf_authors",
        target_dim="D8",
        level="third",
        certainty=0.4,
        description_template=(
            "{left_doc}的银行账号字面「{value}」与{right_doc}的PDF作者一致，"
            "属异常巧合，建议复核"
        ),
        legal_reference=None,
        value_kind="generic",
        symmetric=True,
    ),

    # 邮箱用户名 ↔ 元数据作者
    CrossFieldRule(
        rule_id="R09",
        risk_pattern="email_user_eq_other_author",
        left_attr="email_locals",
        right_attr="pdf_authors",
        target_dim="D8",
        level="second",
        certainty=0.6,
        description_template=(
            "{left_doc}的邮箱用户名「{value}」与{right_doc}的PDF作者一致，"
            "疑似同一身份制作多份投标"
        ),
        legal_reference=_LEGAL_REF_LAW40_6,
        value_kind="generic",
        symmetric=True,
    ),
]


# ── 字段中文标签（用于自然语言描述） ──

_FIELD_LABELS: Final[dict[str, str]] = {
    "company_info.company_name": "投标公司名称",
    "company_info.legal_representative": "法定代表人",
    "company_info.contact_phone": "联系电话",
    "company_info.bank_account": "银行账号",
    "company_info.unified_social_credit_code": "统一信用代码",
    "personnel_list[].name": "项目人员",
    "personnel_list[].certificate_numbers": "项目人员资格证书",
    "personnel_list[].phone": "项目人员手机",
    "personnel_list[].email": "项目人员邮箱",
    "subcontractors": "分包商/供应商",
    "metadata_info.author": "PDF作者",
    "metadata_info.creator_tool": "PDF生成工具",
    "metadata_info.producer": "PDF生成器",
    "metadata_info.title": "PDF标题",
}


def field_label(field_path: str) -> str:
    return _FIELD_LABELS.get(field_path, field_path)


# ── 引擎 ──

class CrossFieldCorrelator:
    """跨字段串联引擎"""

    def __init__(
        self,
        indices: dict[int, IdentifierIndex],
        doc_names: Optional[dict[int, str]] = None,
        tender_company_blacklist: Optional[set[str]] = None,
    ) -> None:
        self._indices = indices
        self._doc_names = doc_names or {
            did: idx.doc_name for did, idx in indices.items()
        }
        # 招标方公司黑名单（避免"采购方名称"误命中投标方分包商）
        self._tender_blacklist = tender_company_blacklist or set()

    def correlate(self) -> list[CrossFieldHit]:
        """对所有 (doc_a, doc_b) pair 执行跨字段命中扫描"""
        hits: list[CrossFieldHit] = []
        doc_ids = sorted(self._indices.keys())

        for id_a, id_b in combinations(doc_ids, 2):
            idx_a = self._indices[id_a]
            idx_b = self._indices[id_b]
            for rule in _RULES:
                hits += self._apply_rule(rule, idx_a, idx_b)
                if rule.symmetric:
                    hits += self._apply_rule(rule, idx_b, idx_a)

        # 去重: 同一 pair + rule_id + matched_value 仅保留一条
        deduped = self._dedup_hits(hits)
        logger.info(
            f"[CrossFieldCorrelator] {len(doc_ids)} 份文件 "
            f"产生 {len(hits)} 条原始命中, 去重后 {len(deduped)} 条"
        )
        return deduped

    def _apply_rule(
        self,
        rule: CrossFieldRule,
        left: IdentifierIndex,
        right: IdentifierIndex,
    ) -> list[CrossFieldHit]:
        """对单条规则在 (left, right) 上执行命中扫描"""
        if left.doc_id == right.doc_id:
            return []

        left_bucket: dict[str, list[FieldRef]] = getattr(left, rule.left_attr, {}) or {}
        right_bucket: dict[str, list[FieldRef]] = getattr(right, rule.right_attr, {}) or {}
        if not left_bucket or not right_bucket:
            return []

        results: list[CrossFieldHit] = []
        for value, left_refs in left_bucket.items():
            if not _is_meaningful_value(value, rule.value_kind):
                continue
            if value in self._tender_blacklist:
                continue
            right_refs = right_bucket.get(value)
            if not right_refs:
                continue

            # 同属性命中（如 phones↔phones）需保证字段路径不同（一个是 contact_phone，
            # 一个是 personnel_list[].phone），否则属于"同字段重叠"由其他模块处理
            if rule.left_attr == rule.right_attr:
                cross_field_pairs = [
                    (lr, rr) for lr in left_refs for rr in right_refs
                    if lr.field_path != rr.field_path
                ]
                if not cross_field_pairs:
                    continue
                left_ref = cross_field_pairs[0][0]
                right_ref = cross_field_pairs[0][1]
            else:
                left_ref = left_refs[0]
                right_ref = right_refs[0]

            description = self._format_description(
                rule, left, right, value, left_ref, right_ref
            )

            results.append(CrossFieldHit(
                pair_id=f"{min(left.doc_id, right.doc_id)}:{max(left.doc_id, right.doc_id)}",
                rule_id=rule.rule_id,
                risk_pattern=rule.risk_pattern,
                target_dim=rule.target_dim,
                level=rule.level,
                certainty=rule.certainty,
                matched_value=value,
                description=description,
                left_doc_id=left.doc_id,
                left_doc_name=left.doc_name,
                left_field=left_ref.field_path,
                left_quote=left_ref.raw_value,
                left_page=left_ref.page,
                right_doc_id=right.doc_id,
                right_doc_name=right.doc_name,
                right_field=right_ref.field_path,
                right_quote=right_ref.raw_value,
                right_page=right_ref.page,
                legal_reference=rule.legal_reference,
                extra={
                    "all_left_fields": [r.field_path for r in left_refs],
                    "all_right_fields": [r.field_path for r in right_refs],
                },
            ))
        return results

    @staticmethod
    def _format_description(
        rule: CrossFieldRule,
        left: IdentifierIndex,
        right: IdentifierIndex,
        value: str,
        left_ref: FieldRef,
        right_ref: FieldRef,
    ) -> str:
        return rule.description_template.format(
            left_doc=left.doc_name,
            right_doc=right.doc_name,
            value=value,
            left_field_label=field_label(left_ref.field_path),
            right_field_label=field_label(right_ref.field_path),
        )

    @staticmethod
    def _dedup_hits(hits: list[CrossFieldHit]) -> list[CrossFieldHit]:
        """去重: 按 (pair_id, rule_id, matched_value) 仅保留首条
        但保留两个方向中证据更强的（按 left_field 字典序稳定）"""
        seen: dict[tuple[str, str, str], CrossFieldHit] = {}
        for h in hits:
            key = (h.pair_id, h.rule_id, h.matched_value)
            existing = seen.get(key)
            if existing is None:
                seen[key] = h
                continue
            # 保留 left_field 字典序更小者（稳定但不强）
            if (h.left_doc_id, h.left_field) < (existing.left_doc_id, existing.left_field):
                seen[key] = h
        return list(seen.values())

    @staticmethod
    def to_findings(hits: list[CrossFieldHit]) -> list[dict]:
        """将 CrossFieldHit 列表转为 GlobalFeatureResult.global_findings 兼容格式"""
        result: list[dict] = []
        for h in hits:
            result.append({
                "type": "cross_field_correlation",
                "involved_doc_ids": [h.left_doc_id, h.right_doc_id],
                "involved_names": [h.left_doc_name, h.right_doc_name],
                "description": h.description,
                "risk_pattern": h.risk_pattern,
                "target_dim": h.target_dim,
                "level": h.level,
                "certainty": h.certainty,
                "legal_reference": h.legal_reference,
                "matched_value": h.matched_value,
                "left_field": h.left_field,
                "right_field": h.right_field,
            })
        return result

    @staticmethod
    def to_pair_findings(
        hits: list[CrossFieldHit],
        pair_doc_a: int,
        pair_doc_b: int,
    ) -> list[dict]:
        """将一个 pair 范围内的 CrossFieldHit 转为 pair-level finding dict
        (与 _extract_structured_findings 输出兼容)"""
        target_pair_id = f"{min(pair_doc_a, pair_doc_b)}:{max(pair_doc_a, pair_doc_b)}"
        result: list[dict] = []
        for h in hits:
            if h.pair_id != target_pair_id:
                continue
            quote_a, page_a, quote_b, page_b = (
                (h.left_quote, h.left_page, h.right_quote, h.right_page)
                if h.left_doc_id == pair_doc_a
                else (h.right_quote, h.right_page, h.left_quote, h.left_page)
            )
            result.append({
                "pair_id": target_pair_id,
                "dimension": h.target_dim,
                "dimension_label": h.target_dim,
                "level": h.level,
                "description": h.description,
                "detail": (
                    f"命中规则 {h.rule_id} ({h.risk_pattern})。"
                    + (f" 法规对照: {h.legal_reference}" if h.legal_reference else "")
                ),
                "evidences": [
                    {
                        "doc_id": h.left_doc_id,
                        "doc_name": h.left_doc_name,
                        "quote": h.left_quote,
                        "location": h.left_page,
                    },
                    {
                        "doc_id": h.right_doc_id,
                        "doc_name": h.right_doc_name,
                        "quote": h.right_quote,
                        "location": h.right_page,
                    },
                ],
                "quote_a": quote_a,
                "page_a": page_a,
                "quote_b": quote_b,
                "page_b": page_b,
                "similarity": None,
                "certainty": h.certainty,
                "verified": True,
                # 跨字段标识
                "risk_pattern": h.risk_pattern,
                "source_field_a": h.left_field if h.left_doc_id == pair_doc_a else h.right_field,
                "source_field_b": h.right_field if h.left_doc_id == pair_doc_a else h.left_field,
                "legal_reference": h.legal_reference,
                "_cross_field": True,
            })
        return result
