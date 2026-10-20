"""一致性后检查 + family rules + domain keywords 纯函数集合（R2-3 Step 2 抽出）

涵盖以下原 `ReviewOrchestrator` 私有方法（沿用「self 不替换 + 去前导下划线」模式）：

- 一致性后检查: `post_review_consistency_check`（含 Gate Pipeline 注入）
- 家族规则: `apply_family_rules` / `detect_family_type` / `derive_attribution_type` /
  `normalize_view_category`
- 缺失关键词: `extract_missing_keywords` / `evidence_covers_missing`
- 领域关键词: `extract_domain_keywords` / `estimate_keyword_relevance`

所有函数第一参数 `self` 接收 `ReviewOrchestrator` 实例，跨方法调用通过 `self.xxx`
触发主类 thin forwarder（兼容 monkey patch / inspect.getsource 锚点）。

模块级常量（与主类 ClassVar 内容 byte-equal，主类 ClassVar 通过 re-export 引用）:
- MISSING_SIGNALS
- GENERIC_STOPWORDS
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import TYPE_CHECKING, Optional

from sqlalchemy import select

from app.models.bid_response import BidResponse
from app.models.requirement import Requirement
from app.models.review_result import ReviewResult

if TYPE_CHECKING:
    from app.core.ai.reviewer import ReviewOrchestrator


# 缺失语义信号（与 ReviewOrchestrator._MISSING_SIGNALS byte-equal）
MISSING_SIGNALS: list[str] = [
    "未提供", "未发现", "缺失", "未附", "未见", "未包含",
    "不包含", "无法确认", "不完整", "未提交",
]


# 通用停用词（与 ReviewOrchestrator._GENERIC_STOPWORDS byte-equal）
GENERIC_STOPWORDS: frozenset[str] = frozenset({
    "提供", "要求", "满足", "具备", "投标", "招标", "项目", "文件",
    "证明", "材料", "相关", "应当", "必须", "能够", "具有", "符合",
    "近", "年", "三年", "以上", "类似", "其他", "包括", "以及",
    "中", "的", "或", "和", "且", "等", "及", "不", "无",
})


# ---------------------------------------------------------------------------
# 家族规则 / 类别 / 归因（纯函数，无 self 内部状态）
# ---------------------------------------------------------------------------


def derive_attribution_type(self: "ReviewOrchestrator", requirement: Optional[Requirement]) -> str:
    """根据 requirement 主体范围推断归因关系。"""
    if not requirement:
        return "none"
    if requirement.subject_scope == "both":
        return "shared"
    if requirement.subject_scope in {"enterprise", "personnel"}:
        return "independent"
    return "none"


def detect_family_type(self: "ReviewOrchestrator", description: str, category: str) -> str:
    text = f"{description or ''} {category or ''}".lower()
    if any(k in text for k in ["业绩", "合同", "类似项目", "performance", "perf"]):
        return "performance"
    if any(k in text for k in ["审计", "发票", "税务", "财务", "audit", "invoice", "tax"]):
        return "audit_finance"
    if any(k in text for k in ["资质", "资格", "证书", "qualification", "qual"]):
        return "qualification"
    return "generic"


def normalize_view_category(self: "ReviewOrchestrator", req: Requirement) -> str:
    """统一类别视图代码。"""
    category = req.category.value if req.category else "generic"
    if category in {"qualification", "technical", "commercial", "performance", "staffing", "format"}:
        return category
    return "generic"


def apply_family_rules(
    self: "ReviewOrchestrator",
    family_type: str,
    evidence_relation: str,
    has_conflict: bool,
) -> tuple[str, str]:
    """返回 (consistency_flag, reconcile_note)。"""
    if evidence_relation == "undetermined":
        return "needs_review", "证据关联信号不足，暂无法判断共享或独立。"
    if family_type == "performance":
        if evidence_relation == "shared" and has_conflict:
            return "conflicted", "业绩同事实组出现冲突（符合/不符合并存），需人工复核。"
        if evidence_relation == "shared":
            return "aligned", "业绩同事实组证据一致，支持企业/人员双归因。"
        return "aligned", "业绩由独立证据支持，不与其他条目冲突。"
    if family_type == "audit_finance":
        if evidence_relation == "shared" and has_conflict:
            return "needs_review", "审计/财务同事实组结论不一致，建议重点核对报告号与附件。"
        if evidence_relation == "shared":
            return "aligned", "审计/财务同报告或同票据证据一致。"
        return "aligned", "审计/财务条目使用独立证据，差异可解释。"
    if family_type == "qualification":
        if evidence_relation == "shared" and has_conflict:
            return "needs_review", "资质证据同源但结论冲突，请核查证书有效期与适用范围。"
        return "aligned", "资质证据关系明确。"
    return "aligned", "证据关系已收敛。"


# ---------------------------------------------------------------------------
# 缺失关键词检测（供 CrossGroupConflictGate evidence 注入）
# ---------------------------------------------------------------------------


def extract_missing_keywords(self: "ReviewOrchestrator", comment: str) -> list[str]:
    """从审查意见中提取被判定为缺失的具体项。

    扫描"未提供XXX"/"缺失XXX"等句式，提取 XXX 作为缺失关键词。
    使用 `self._MISSING_SIGNALS`（主类 ClassVar）以兼容子类覆盖。
    """
    if not comment:
        return []
    keywords: list[str] = []
    for signal in self._MISSING_SIGNALS:
        idx = 0
        while True:
            pos = comment.find(signal, idx)
            if pos < 0:
                break
            tail = comment[pos + len(signal):pos + len(signal) + 20]
            tail = tail.split("，")[0].split("。")[0].split("；")[0].strip()
            cn_match = re.findall(r"[\u4e00-\u9fff]{2,8}", tail)
            if cn_match:
                keywords.append(cn_match[0])
            idx = pos + len(signal)
    return keywords


def evidence_covers_missing(
    self: "ReviewOrchestrator",
    br: Optional[BidResponse],
    rr: ReviewResult,
    missing_kws: list[str],
) -> bool:
    """检查 BidResponse/ReviewResult 的内容是否覆盖了缺失关键词。"""
    if not missing_kws:
        return False
    pool = " ".join([
        br.content or "" if br else "",
        rr.response_summary or "",
        rr.comment or "",
    ])
    return any(kw in pool for kw in missing_kws)


# ---------------------------------------------------------------------------
# 领域关键词重叠度估算（供 RelevanceAnomalyBackfillGate evidence 注入）
# ---------------------------------------------------------------------------


def estimate_keyword_relevance(
    self: "ReviewOrchestrator",
    requirement_desc: str,
    response_content: str,
) -> int:
    """基于关键词重叠度估算关联性(0-100), 不依赖 LLM。

    算法: 从 requirement 描述中提取领域关键词(2-4字滑窗, 去除通用词),
    计算在 response 内容中的命中比例。
    """
    req_words = set(self._extract_domain_keywords(requirement_desc))
    if not req_words:
        return 80

    resp_text = response_content
    hit = sum(1 for w in req_words if w in resp_text)
    ratio = hit / len(req_words)
    return min(100, int(ratio * 120))


def extract_domain_keywords(self: "ReviewOrchestrator", text: str) -> list[str]:
    """从文本中提取领域关键词(2-4字 n-gram, 去除通用词)。

    不依赖外部分词库, 使用滑动窗口生成候选词,
    仅保留不含通用停用词的候选。使用 `self._GENERIC_STOPWORDS`（主类 ClassVar）。
    """
    keywords: list[str] = []
    stopwords = self._GENERIC_STOPWORDS
    for n in (4, 3, 2):
        for i in range(len(text) - n + 1):
            gram = text[i:i + n]
            if gram in stopwords:
                continue
            if any(sw in gram for sw in stopwords if len(sw) < n):
                continue
            if all('\u4e00' <= c <= '\u9fff' or c.isalnum() for c in gram):
                keywords.append(gram)
    seen: set[str] = set()
    unique: list[str] = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            unique.append(kw)
    return unique[:30]


# ---------------------------------------------------------------------------
# 一致性后检查主入口（Gate Pipeline 驱动）
# ---------------------------------------------------------------------------


async def post_review_consistency_check(self: "ReviewOrchestrator", project_id: int) -> None:
    """审查后全局一致性收敛（可重入）。

    流程:
        1. 短 session 拉取 ReviewResult + Requirement + BidResponse 三元组
        2. 按 evidence_anchor 派生 fact_key 分组（self._derive_evidence_based_key）
        3. 事实层主判定（self._judge_fact_then_project）
        4. Gate Pipeline 驱动 CrossGroupConflictGate / RelevanceAnomalyBackfillGate
        5. 构建事实图（self._build_fact_graph）

    所有跨子模块调用走 `self.xxx` 触发主类 thin forwarder，兼容 monkey patch。
    """
    from app.core.database import async_session_factory

    async with async_session_factory() as session:
        stmt = (
            select(ReviewResult, Requirement, BidResponse)
            .join(Requirement, ReviewResult.requirement_id == Requirement.id)
            .outerjoin(BidResponse, ReviewResult.bid_response_id == BidResponse.id)
            .where(ReviewResult.project_id == project_id)
        )
        rows = (await session.execute(stmt)).all()
        if not rows:
            return

        grouped: dict[str, list[tuple[ReviewResult, Requirement, Optional[BidResponse]]]] = defaultdict(list)
        for rr, req, br in rows:
            evidence_key = self._derive_evidence_based_key(br)
            fact_key = (
                evidence_key
                or rr.fact_key
                or rr.canonical_key
                or req.canonical_key
                or f"req-{rr.requirement_id}"
            )
            rr.fact_key = fact_key
            rr.canonical_key = rr.canonical_key or fact_key
            rr.family_type = rr.family_type or self._detect_family_type(req.description, req.category.value)
            grouped[fact_key].append((rr, req, br))

        self._judge_fact_then_project(grouped)

        # Fix-01: Gate Pipeline 为唯一 post_pipeline 路径。
        # CrossGroupConflictGate / RelevanceAnomalyBackfillGate 由 Pipeline 驱动；
        # helper 方法（_extract_missing_keywords / _evidence_covers_missing /
        # _estimate_keyword_relevance）通过 evidence dict 注入供 Gate 调用。
        from app.core.ai.gates import build_pipeline_for
        from app.core.ai.gates.base import GateContext

        _evidence_pp = {
            "extract_missing_keywords": self._extract_missing_keywords,
            "evidence_covers_missing": self._evidence_covers_missing,
            "estimate_keyword_relevance": self._estimate_keyword_relevance,
        }
        _ctx_pp = GateContext(
            flow="investigate", stage="post_pipeline",
            target=dict(grouped), evidence=_evidence_pp,
            config={"project_id": project_id},
        )
        _pipeline_pp = build_pipeline_for("investigate")
        _pipeline_pp.run(_ctx_pp, apply_patch_to=dict(grouped))

        await self._build_fact_graph(session, project_id, grouped)
        await session.commit()
