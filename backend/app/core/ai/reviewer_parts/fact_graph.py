"""事实图构造 + 证据关系（R2-3 Step 3 抽出）

涵盖以下原 `ReviewOrchestrator` 私有方法：

- 证据键派生 / 事实主判定 / 事实图落库:
  `derive_evidence_based_key` / `select_fact_primary_judgement` / `build_fact_graph`
- 证据关系/锚点工具:
  `extract_anchor_tokens` / `parse_source_pages` / `compute_evidence_relation`
- 事实层投影:
  `judge_fact_then_project`

所有函数第一参数 `self` 接收 `ReviewOrchestrator` 实例，跨方法调用通过 `self.xxx`
触发主类 thin forwarder（兼容 monkey patch + 测试侧实例方法访问）。

外部测试侧已知调用路径:
- `reviewer._compute_evidence_relation(items)`（test_review_consistency_reconcile）
- `reviewer._judge_fact_then_project(grouped)`（test_review_consistency_reconcile）
→ 主类必须保留同名 thin forwarder。
"""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bid_response import BidResponse
from app.models.requirement import Requirement
from app.models.review_fact import ReviewFact, ReviewFactJudgement, ReviewFactLink
from app.models.review_result import ComplianceLevel, ReviewResult, RiskLevel

if TYPE_CHECKING:
    from app.core.ai.reviewer import ReviewOrchestrator


# ---------------------------------------------------------------------------
# 锚点 / 页码 工具（纯函数）
# ---------------------------------------------------------------------------


def extract_anchor_tokens(self: "ReviewOrchestrator", text: str) -> set[str]:
    if not text:
        return set()
    tokens = set(re.findall(r"[A-Za-z0-9\-]{6,}", text))
    years = re.findall(r"20\d{2}", text)
    amounts = re.findall(r"\d+(?:\.\d+)?\s*(?:亿|万元|万|元)", text)
    for y in years[:3]:
        tokens.add(y)
    for a in amounts[:3]:
        tokens.add(a)
    return tokens


def parse_source_pages(self: "ReviewOrchestrator", pages: Optional[str]) -> set[int]:
    if not pages:
        return set()
    page_nums: set[int] = set()
    for p in pages.split(","):
        p = p.strip()
        if p.isdigit():
            page_nums.add(int(p))
    return page_nums


# ---------------------------------------------------------------------------
# 证据关系（外部测试 reviewer._compute_evidence_relation 实例调用入口）
# ---------------------------------------------------------------------------


def compute_evidence_relation(
    self: "ReviewOrchestrator",
    items: list[tuple[ReviewResult, Requirement, Optional[BidResponse]]],
) -> str:
    if len(items) <= 1:
        # 单事实默认不触发复核，避免"完全符合+待复核"假阳性。
        return "independent"
    token_sets: list[set[str]] = []
    page_sets: list[set[int]] = []
    scope_values: list[str] = []
    for _, req, br in items:
        anchor_text = ""
        if br:
            anchor_text = " ".join(
                [
                    br.evidence_anchor or "",
                    br.content or "",
                ]
            )
            page_sets.append(self._parse_source_pages(br.source_pages))
        token_sets.append(self._extract_anchor_tokens(anchor_text + " " + (req.description or "")))
        scope_values.append(req.subject_scope or "generic")

    token_overlap = 0
    for i in range(len(token_sets)):
        for j in range(i + 1, len(token_sets)):
            if token_sets[i] and token_sets[j] and token_sets[i] & token_sets[j]:
                token_overlap += 1
    page_near = 0
    for i in range(len(page_sets)):
        for j in range(i + 1, len(page_sets)):
            if not page_sets[i] or not page_sets[j]:
                continue
            if page_sets[i] & page_sets[j]:
                page_near += 1
                continue
            min_gap = min(abs(a - b) for a in page_sets[i] for b in page_sets[j])
            if min_gap <= 2:
                page_near += 1
    has_both_scope = any(s in {"enterprise", "both"} for s in scope_values) and any(
        s in {"personnel", "both"} for s in scope_values
    )
    if token_overlap >= 1 or page_near >= 1:
        return "shared"
    if has_both_scope:
        return "undetermined"
    return "independent"


# ---------------------------------------------------------------------------
# 事实主判定（按风险等级 + 得分排序）
# ---------------------------------------------------------------------------


def select_fact_primary_judgement(
    self: "ReviewOrchestrator",
    items: list[tuple[ReviewResult, Requirement, Optional[BidResponse]]],
) -> tuple[str | None, int | None, str | None, str | None]:
    """事实层主判定：优先高风险，再按最低得分。"""
    if not items:
        return None, None, None, None
    ordered = sorted(
        items,
        key=lambda row: (
            0 if row[0].risk_level == RiskLevel.HIGH else 1,
            row[0].score or 0,
        ),
    )
    rr = ordered[0][0]
    return (
        rr.compliance_level.value if hasattr(rr.compliance_level, "value") else str(rr.compliance_level),
        rr.score,
        rr.risk_level.value if hasattr(rr.risk_level, "value") else str(rr.risk_level),
        rr.comment,
    )


# ---------------------------------------------------------------------------
# 事实图构造（DB 写）
# ---------------------------------------------------------------------------


async def build_fact_graph(
    self: "ReviewOrchestrator",
    session: AsyncSession,
    project_id: int,
    grouped: dict[str, list[tuple[ReviewResult, Requirement, Optional[BidResponse]]]],
) -> None:
    """构建事实图：事实主表 + 事实映射 + 事实视图判定。"""
    await session.execute(
        delete(ReviewFactJudgement).where(
            ReviewFactJudgement.fact_id.in_(
                select(ReviewFact.id).where(ReviewFact.project_id == project_id)
            )
        )
    )
    await session.execute(
        delete(ReviewFactLink).where(
            ReviewFactLink.fact_id.in_(
                select(ReviewFact.id).where(ReviewFact.project_id == project_id)
            )
        )
    )
    await session.execute(delete(ReviewFact).where(ReviewFact.project_id == project_id))
    await session.flush()

    for fact_uid, items in grouped.items():
        sample_rr, sample_req, sample_br = items[0]
        comp, score, risk, note = self._select_fact_primary_judgement(items)
        fact = ReviewFact(
            project_id=project_id,
            fact_uid=fact_uid,
            topic=sample_req.topic,
            family_type=sample_rr.family_type or self._detect_family_type(sample_req.description, sample_req.category.value),
            anchor_text=(sample_br.evidence_anchor if sample_br else None),
            source_pages=(sample_br.source_pages if sample_br else None),
            confidence=1.0 if len(items) > 1 else 0.75,
            primary_compliance_level=comp,
            primary_score=score,
            primary_risk_level=risk,
            primary_note=note,
            status=sample_rr.consistency_flag or "aligned",
        )
        session.add(fact)
        await session.flush()

        for rr, req, br in items:
            session.add(
                ReviewFactLink(
                    fact_id=fact.id,
                    requirement_id=req.id,
                    bid_response_id=(br.id if br else None),
                    category=self._normalize_view_category(req),
                    subject_scope=req.subject_scope,
                    link_role="pair",
                    evidence_anchor=(br.evidence_anchor if br else None),
                    source_pages=(br.source_pages if br else None),
                )
            )
            session.add(
                ReviewFactJudgement(
                    fact_id=fact.id,
                    requirement_id=req.id,
                    review_result_id=rr.id,
                    view_category=self._normalize_view_category(req),
                    compliance_level=rr.compliance_level.value if hasattr(rr.compliance_level, "value") else str(rr.compliance_level),
                    score=rr.score,
                    risk_level=rr.risk_level.value if hasattr(rr.risk_level, "value") else str(rr.risk_level),
                    is_primary=0,
                    note=rr.reconcile_note,
                )
            )


# ---------------------------------------------------------------------------
# 事实层投影（外部测试 reviewer._judge_fact_then_project 实例调用入口）
# ---------------------------------------------------------------------------


def judge_fact_then_project(
    self: "ReviewOrchestrator",
    grouped: dict[str, list[tuple[ReviewResult, Requirement, Optional[BidResponse]]]],
) -> None:
    """先事实判定，再投影类别视图结果。"""
    for key, items in grouped.items():
        evidence_relation = self._compute_evidence_relation(items)
        family_type = next((rr.family_type for rr, _, _ in items if rr.family_type), "generic")
        has_enterprise_scope = any(req.subject_scope in {"enterprise", "both"} for _, req, _ in items)
        has_personnel_scope = any(req.subject_scope in {"personnel", "both"} for _, req, _ in items)
        has_dual_scope = has_enterprise_scope and has_personnel_scope
        has_fully = any(rr.compliance_level == ComplianceLevel.FULLY for rr, _, _ in items)
        has_not_or_missing = any(
            rr.compliance_level in {ComplianceLevel.NOT, ComplianceLevel.MISSING}
            for rr, _, _ in items
        )
        conflict = has_fully and has_not_or_missing

        if (
            family_type in {"performance", "qualification", "audit_finance"}
            and evidence_relation == "undetermined"
            and has_dual_scope
            and not conflict
        ):
            evidence_relation = "shared"

        consistency_flag, reconcile_note = self._apply_family_rules(
            family_type=family_type,
            evidence_relation=evidence_relation,
            has_conflict=conflict,
        )

        for rr, req, _ in items:
            rr.canonical_key = key
            rr.fact_key = key
            rr.evidence_relation = evidence_relation
            rr.attribution_type = rr.attribution_type or self._derive_attribution_type(req)
            rr.consistency_flag = consistency_flag
            rr.reconcile_note = reconcile_note
            if consistency_flag == "conflicted" and "事实组冲突" not in (rr.comment or ""):
                rr.comment = f"{rr.comment}；事实组冲突，建议人工复核"
            if consistency_flag == "needs_review" and "证据关联不足" not in (rr.comment or ""):
                rr.comment = f"{rr.comment}；证据关联不足，待复核"


# ---------------------------------------------------------------------------
# 证据键派生（短摘要 hash）
# ---------------------------------------------------------------------------


def derive_evidence_based_key(self: "ReviewOrchestrator", br: Optional[BidResponse]) -> str:
    """从 BidResponse 证据锚点派生事实键。

    优先级: evidence_anchor > 合同号+项目名+年份+金额+人员名 > 页码簇。
    返回空字符串表示无法从证据派生。
    """
    if not br:
        return ""
    anchor = br.evidence_anchor or ""
    content = br.content or ""
    pages = br.source_pages or ""
    text = f"{anchor} {content}"

    contract = re.findall(r"[A-Za-z0-9\-]{6,}", anchor) if anchor else []
    years = re.findall(r"20\d{2}", text)
    amounts = re.findall(r"\d+(?:\.\d+)?\s*(?:亿|万元|万|元)", text)
    persons = re.findall(
        r"[\u4e00-\u9fff]{2,4}(?=的|负责|经理|主管|同志)",
        text,
    )

    parts = [
        contract[0] if contract else "",
        ",".join(sorted(set(years))[:2]),
        amounts[0] if amounts else "",
        ",".join(persons[:2]),
    ]
    sig = "|".join(p for p in parts if p)
    if not sig:
        if pages and len(pages) > 3:
            sig = f"pages:{pages[:40]}"
        else:
            return ""
    digest = hashlib.md5(sig.encode("utf-8")).hexdigest()[:10]
    return f"ev:{digest}"
