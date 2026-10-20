""" 防御测试: 可评审性消费闸门统一口径。

映射真实问题:
- 参考条款折叠仅前端展示生效, confirm-all / 响应提取 / 匹配 / 审查 / 进度 / 重试
 仍按 confirmed=1 全量消费, 折叠项被审查打 0 分拉低评分 → 闸门谓词收口
 review_contracts, 各消费点统一引用。
- 评分项铁律(用户明确约束): scoring_item / is_key_review 无条件通过闸门,
 任何情况下(包括 review_relevance 被误改)不得被排除出审查与计分。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
os.environ.setdefault("LOG_LEVEL", "DEBUG")
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from app.core.config import settings  # noqa: E402
from app.core.review_contracts import (  # noqa: E402
    actionable_requirement_condition,
    counts_toward_review_summary,
    is_reviewable_requirement,
    reviewable_requirement_conditions,
)

pytestmark = pytest.mark.scoring


def _req(**overrides):
    base = dict(
        confirmed=1,
        review_relevance="actionable",
        requirement_kind="normal",
        is_key_review=0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# 行级判定 is_reviewable_requirement
# ---------------------------------------------------------------------------


def test_unconfirmed_never_reviewable(monkeypatch):
    monkeypatch.setattr(settings, "REVIEW_ACTIONABLE_GATE_ENABLED", True)
    assert is_reviewable_requirement(_req(confirmed=0)) is False
    # 即使是评分项, 未确认也不进入审查(确认流不变)
    assert is_reviewable_requirement(
        _req(confirmed=0, requirement_kind="scoring_item")
    ) is False


def test_actionable_confirmed_is_reviewable(monkeypatch):
    monkeypatch.setattr(settings, "REVIEW_ACTIONABLE_GATE_ENABLED", True)
    assert is_reviewable_requirement(_req()) is True


def test_folded_reference_not_reviewable(monkeypatch):
    monkeypatch.setattr(settings, "REVIEW_ACTIONABLE_GATE_ENABLED", True)
    assert is_reviewable_requirement(_req(review_relevance="informational")) is False
    assert is_reviewable_requirement(_req(review_relevance="procedural")) is False


def test_scoring_item_always_reviewable_even_if_mislabeled(monkeypatch):
    # 评分项铁律: 即使 review_relevance 被任何路径误改, 评分项也必须进入审查
    monkeypatch.setattr(settings, "REVIEW_ACTIONABLE_GATE_ENABLED", True)
    assert is_reviewable_requirement(
        _req(requirement_kind="scoring_item", review_relevance="informational")
    ) is True
    assert is_reviewable_requirement(
        _req(requirement_kind="scoring_item", review_relevance="procedural")
    ) is True


def test_key_review_always_reviewable(monkeypatch):
    # 重点评审项(含分值正文条款)同评分项铁律
    monkeypatch.setattr(settings, "REVIEW_ACTIONABLE_GATE_ENABLED", True)
    assert is_reviewable_requirement(
        _req(is_key_review=1, review_relevance="procedural")
    ) is True


def test_missing_relevance_defaults_to_reviewable(monkeypatch):
    # 历史数据 review_relevance 为 None → 视为 actionable(不误伤存量)
    monkeypatch.setattr(settings, "REVIEW_ACTIONABLE_GATE_ENABLED", True)
    assert is_reviewable_requirement(_req(review_relevance=None)) is True


def test_gate_disabled_falls_back_to_confirmed_only(monkeypatch):
    monkeypatch.setattr(settings, "REVIEW_ACTIONABLE_GATE_ENABLED", False)
    assert is_reviewable_requirement(_req(review_relevance="procedural")) is True
    assert is_reviewable_requirement(_req(confirmed=0)) is False


# ---------------------------------------------------------------------------
# SQL 条件构造(与行级判定语义一致)
# ---------------------------------------------------------------------------


def test_condition_none_when_gate_disabled(monkeypatch):
    monkeypatch.setattr(settings, "REVIEW_ACTIONABLE_GATE_ENABLED", False)
    assert actionable_requirement_condition() is None
    assert len(reviewable_requirement_conditions()) == 1


def test_conditions_compile_covers_scoring_exemption(monkeypatch):
    # 编译后的 SQL 必须同时覆盖: confirmed 基线 + relevance 过滤 + 评分项豁免
    from sqlalchemy import select

    from app.models.requirement import Requirement

    monkeypatch.setattr(settings, "REVIEW_ACTIONABLE_GATE_ENABLED", True)
    conditions = reviewable_requirement_conditions()
    assert len(conditions) == 2
    stmt = select(Requirement.id).where(*conditions)
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "confirmed" in sql
    assert "review_relevance" in sql
    assert "scoring_item" in sql
    assert "is_key_review" in sql


# ---------------------------------------------------------------------------
# 聚合计分谓词 counts_toward_review_summary (P2.2 聚合动态排除)
# ---------------------------------------------------------------------------


def test_summary_counts_actionable_and_excludes_reference(monkeypatch):
    monkeypatch.setattr(settings, "REVIEW_ACTIONABLE_GATE_ENABLED", True)
    assert counts_toward_review_summary(
        review_relevance="actionable", requirement_kind="normal", is_key_review=0
    ) is True
    # 人工降级/存量折叠行: 不计入汇总评分(无需重审即修正评分)
    assert counts_toward_review_summary(
        review_relevance="procedural", requirement_kind="normal", is_key_review=0
    ) is False
    assert counts_toward_review_summary(
        review_relevance="informational", requirement_kind="normal", is_key_review=0
    ) is False


def test_summary_scoring_iron_rule_never_excluded(monkeypatch):
    # 评分项铁律(用户明确约束): 评分项/重点评审项任何情况下恒计入评分
    monkeypatch.setattr(settings, "REVIEW_ACTIONABLE_GATE_ENABLED", True)
    assert counts_toward_review_summary(
        review_relevance="procedural", requirement_kind="scoring_item", is_key_review=0
    ) is True
    assert counts_toward_review_summary(
        review_relevance="informational", requirement_kind="normal", is_key_review=1
    ) is True


def test_summary_counts_all_when_gate_disabled(monkeypatch):
    monkeypatch.setattr(settings, "REVIEW_ACTIONABLE_GATE_ENABLED", False)
    assert counts_toward_review_summary(
        review_relevance="procedural", requirement_kind="normal", is_key_review=0
    ) is True


def test_summary_missing_relevance_counts(monkeypatch):
    # 历史数据 review_relevance 为 None → 视为 actionable, 照常计分
    monkeypatch.setattr(settings, "REVIEW_ACTIONABLE_GATE_ENABLED", True)
    assert counts_toward_review_summary(
        review_relevance=None, requirement_kind="normal", is_key_review=0
    ) is True


def test_calculate_review_summary_excludes_demoted_reference(monkeypatch):
    # 端到端: 汇总函数按 requirement 当前 relevance 排除参考条款(评分项恒计入)
    monkeypatch.setattr(settings, "REVIEW_ACTIONABLE_GATE_ENABLED", True)
    from app.services.review_service import calculate_review_summary

    def _result(score, relevance, kind="normal", key=0, risk="none", level="fully", compliant=True):
        return SimpleNamespace(
            requirement=SimpleNamespace(
                category=SimpleNamespace(value="technical"),
                review_relevance=relevance,
                requirement_kind=kind,
                is_key_review=key,
                score_weight=None,
                scoring_metadata=None,
            ),
            category=None,
            is_compliant=compliant,
            compliance_level=level,
            risk_level=risk,
            score=score,
            estimated_score=None,
            comment="",
        )

    results = [
        _result(90, "actionable"),
        # 被降级的参考条款(曾被审为 missing 0分): 必须从汇总排除
        _result(0, "procedural", risk="high", level="missing", compliant=False),
        # 评分项即使被误改 relevance 也恒计入
        _result(80, "informational", kind="scoring_item", key=1),
    ]
    summary = calculate_review_summary(project_id=1, results=results)
    assert summary["total_count"] == 2, "参考条款明细不得计入汇总条数"
    assert summary["high_risk_count"] == 0, "被降级参考条款的高风险不得再拉低结论"
    assert summary["average_score"] == 85.0


# ---------------------------------------------------------------------------
# 消费点接线契约: 链路文件必须引用统一口径 helper(防止回退为裸 confirmed==1)
# ---------------------------------------------------------------------------


_CONSUMER_WIRING = {
    "app/services/requirement_service.py": "actionable_requirement_condition",
    "app/services/review_service.py": "reviewable_requirement_conditions",
    "app/core/extractor/bid_extractor.py": "reviewable_requirement_conditions",
    "app/core/matcher/requirement_matcher.py": "reviewable_requirement_conditions",
    "app/services/flow_retry_service.py": "reviewable_requirement_conditions",
}


def test_consumers_wired_to_shared_gate():
    backend_root = Path(__file__).parent.parent
    for rel_path, symbol in _CONSUMER_WIRING.items():
        source = (backend_root / rel_path).read_text(encoding="utf-8")
        assert symbol in source, f"{rel_path} 未接入统一可评审口径 {symbol}"


def test_bid_extractor_single_path_uses_row_level_gate():
    # 单条提取路径必须做行级闸门检查(参考条款不参与响应提取)
    backend_root = Path(__file__).parent.parent
    source = (backend_root / "app/core/extractor/bid_extractor.py").read_text(
        encoding="utf-8"
    )
    assert "*reviewable_requirement_conditions()" in source
