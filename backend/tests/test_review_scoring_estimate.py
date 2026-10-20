"""P2 闭环防御测试: 评分项预估得分 clamp + 汇总总分(按卷)。

映射真实诉求:
- 结果页需展示评分项"满分/预估得分"并汇总"预估总得分 X/Y"。
- 预估得分必须 clamp 到 [0, 满分]; 无响应=0; 非评分项不预估。
- 汇总按卷(技术标/商务标)聚合, AI 预估仅供参考。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
os.environ.setdefault("LOG_LEVEL", "DEBUG")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.review_contracts import (  # noqa: E402
    compute_estimated_score,
    compute_scoring_aggregate,
    is_scoring_universe_member,
    parse_estimated_score_from_comment,
)
from app.core.ai.reviewer_parts.result_builder import _resolve_estimated_score  # noqa: E402
from app.core.ai.reviewer_parts.result_builder import create_review_result  # noqa: E402
from app.models.review_result import ComplianceLevel  # noqa: E402
from app.schemas.review import ExtractedReviewResult  # noqa: E402

import pytest

pytestmark = pytest.mark.scoring


# ---------------------------------------------------------------------------
# 从审查意见兜底解析预估得分(修复徽标"—"与意见"11分"不一致)
# ---------------------------------------------------------------------------


def test_parse_estimated_from_comment_real_case():
    comment = (
        "投标文件第66-72页提供了设备清单及照片;缺少ICP和电化学工作站两项设备,应扣减4分;"
        "且证明材料无公章...预估得分：11分（满分15分，扣4分）。"
    )
    assert parse_estimated_score_from_comment(comment, 15.0) == 11.0


def test_parse_estimated_clamps_and_handles_slash_format():
    assert parse_estimated_score_from_comment("预估评标 8 / 满分 10", 10.0) == 8.0
    assert parse_estimated_score_from_comment("预估得分：20分", 15.0) == 15.0  # clamp
    assert parse_estimated_score_from_comment("无预估信息") is None
    assert parse_estimated_score_from_comment(None) is None


def test_parse_estimated_real_low_score_phrasings():
    """ 复发: 覆盖 LLM 在不符合/低分场景的真实高频表述(无"预估"前缀)。"""
    # 结论式"得分X分"(图中真实意见)
    assert parse_estimated_score_from_comment("故判定为不符合，得分0分。", 6.0) == 0.0
    assert parse_estimated_score_from_comment("缺失核心材料……得分0分。", 6.0) == 0.0
    assert parse_estimated_score_from_comment("得分：4分", 10.0) == 4.0
    assert parse_estimated_score_from_comment("得分为4分", 10.0) == 4.0
    # 简略式"得X分"
    assert parse_estimated_score_from_comment("综合评估得4分", 10.0) == 4.0
    assert parse_estimated_score_from_comment("应扣减4分，故得分11分", 15.0) == 11.0
    # 无数字"不得分" -> 0(评分项不符合即不得分)
    assert parse_estimated_score_from_comment("本项不符合，不得分。", 10.0) == 0.0
    assert parse_estimated_score_from_comment("未得分", 10.0) == 0.0


def test_parse_estimated_rejects_noise_numbers():
    """防误伤: 满分/扣分/页码/字数/可得分点 等噪声不得被误解析为得分。"""
    assert parse_estimated_score_from_comment("招标要求满分15分，扣4分。", 15.0) is None
    assert parse_estimated_score_from_comment("投标文件第466、468、470页提供凭证。", 10.0) is None
    assert parse_estimated_score_from_comment("检测到相关响应内容（长度111字）。", 10.0) is None
    assert parse_estimated_score_from_comment("响应较充分，可得分点齐全。", 10.0) is None


class _Ext:
    def __init__(self, estimated_score=None, comment=""):
        self.estimated_score = estimated_score
        self.comment = comment


class _Pair:
    def __init__(self, kind="scoring_item", max_score=15.0):
        self.requirement = type("R", (), {"requirement_kind": kind, "score_weight": max_score})()


def test_resolver_falls_back_to_comment_when_field_missing():
    # 结构化字段缺失但意见含"预估得分:11分" -> 解析 11(修复两处不一致)
    val = _resolve_estimated_score(
        _Ext(estimated_score=None, comment="...预估得分：11分（满分15分，扣4分）"),
        _Pair(max_score=15.0),
        ComplianceLevel.PARTIALLY,
    )
    assert val == 11.0


def test_create_review_result_records_rule_assessment_score_mismatch(monkeypatch):
    """逐规则 points 合计与 estimated_score 不一致时，应写入 reconcile_note 供审计。"""
    import asyncio

    class _Pipeline:
        def run(self, _ctx, apply_patch_to=None):
            return []

    class _Self:
        def _compute_algorithm_evidence(self, _pair):
            return {}

        async def _collect_chunk_pages_for_pair(self, _pair, session=None):
            return []

        def _maybe_enforce_algorithmic_risk_band(self, _extracted, _verdicts):
            return None

        def _detect_family_type(self, _desc, _category):
            return "generic"

        def _derive_attribution_type(self, _requirement):
            return "none"

    monkeypatch.setattr(
        "app.core.ai.gates.build_pipeline_for",
        lambda _flow: _Pipeline(),
    )
    requirement = SimpleNamespace(
        description="技术方案评分",
        canonical_key="tech-plan",
        main_requirement_id=None,
        requirement_kind="scoring_item",
        score_weight=10.0,
        scoring_metadata={"max_score": 10.0},
    )
    pair = SimpleNamespace(
        requirement=requirement,
        response=None,
        requirement_id=1,
        response_id=None,
        is_mandatory=False,
        has_response=True,
        category="technical",
    )
    extracted = ExtractedReviewResult(
        requirement_id=1,
        bid_response_id=None,
        category="TECH",
        requirement_summary="技术方案",
        response_summary="已响应",
        is_compliant=True,
        compliance_level="fully",
        score=90,
        comment="投标文件响应较充分，预估得分：5分",
        risk_level="none",
        estimated_score=5.0,
        rule_assessments=[
            {
                "rule_excerpt": "方案完整得10分",
                "status": "met",
                "points": 9.0,
                "reason": "逐规则判定给出9分",
                "evidence_pages": [],
            }
        ],
    )

    result = asyncio.run(create_review_result(_Self(), 1, pair, extracted))

    assert result.estimated_score == 5.0
    assert result.rule_assessments is not None
    assert "不一致" in (result.reconcile_note or "")


# ---------------------------------------------------------------------------
# compute_scoring_aggregate (汇总按卷)
# ---------------------------------------------------------------------------


def _si(max_score, est, volume, *, volume_weight=None, volume_max=None, volume_label=None):
    return {
        "requirement_kind": "scoring_item",
        "max_score": max_score,
        "estimated_score": est,
        "volume": volume,
        "volume_label": volume_label,
        "volume_weight": volume_weight,
        "volume_max": volume_max,
    }


def test_aggregate_totals_and_by_volume():
    items = [
        _si(15, 11, "technical"),
        _si(40, 30, "commercial"),
        _si(10, None, "technical"),  # 未预估: 计入满分不计入预估
        {"requirement_kind": "normal", "max_score": None, "estimated_score": None, "volume": "technical"},
    ]
    agg = compute_scoring_aggregate(items)
    assert agg["scoring_item_count"] == 3
    assert agg["estimated_item_count"] == 2  # 仅 2 项有预估(第三项 None)
    assert agg["max_total_score"] == 65
    assert agg["estimated_total_score"] == 41  # 11 + 30, None 跳过
    tech = agg["estimated_score_by_volume"]["技术标"]
    comm = agg["estimated_score_by_volume"]["商务标"]
    assert tech["max"] == 25 and tech["estimated"] == 11 and tech["count"] == 2
    assert comm["max"] == 40 and comm["estimated"] == 30 and comm["count"] == 1


def test_aggregate_prefers_original_volume_label_for_display_bucket():
    """: 后端汇总桶键优先用表内原文卷名, 缺失时才回退枚举中文映射。"""
    agg = compute_scoring_aggregate([
        _si(30, 28, "commercial", volume_label="商务评审"),
        _si(4, 4, "commercial", volume_label="商务评审"),
        _si(66, 60, "technical", volume_label="技术方案评审"),
    ])

    assert set(agg["estimated_score_by_volume"]) == {"商务评审", "技术方案评审"}
    assert agg["estimated_score_by_volume"]["商务评审"]["max"] == 34


def test_aggregate_inherits_unique_original_label_for_mixed_old_new_rows():
    """: 同一标准卷内新旧元数据混合时, 缺 volume_label 行继承唯一原文标签, 不拆卷。"""
    agg = compute_scoring_aggregate([
        _si(30, 0, "commercial", volume_label="商务部分"),
        _si(4, 0, "commercial"),
        _si(58, 0, "technical", volume_label="技术部分"),
        _si(8, 0, "technical"),
    ])

    assert set(agg["estimated_score_by_volume"]) == {"商务部分", "技术部分"}
    assert agg["estimated_score_by_volume"]["商务部分"]["max"] == 34
    assert agg["estimated_score_by_volume"]["技术部分"]["max"] == 66


def test_aggregate_ignores_non_scoring():
    items = [{"requirement_kind": "normal", "max_score": 10, "estimated_score": 5, "volume": "technical"}]
    agg = compute_scoring_aggregate(items)
    assert agg["scoring_item_count"] == 0
    assert agg["estimated_item_count"] == 0
    assert agg["max_total_score"] == 0
    assert agg["estimated_total_score"] == 0
    assert agg["estimated_score_by_volume"] == {}


def test_aggregate_all_null_estimates_flagged():
    # 全部未预估(1491 旧数据场景): estimated_item_count=0, 满分仍合计
    items = [_si(15, None, "technical"), _si(40, None, "commercial")]
    agg = compute_scoring_aggregate(items)
    assert agg["scoring_item_count"] == 2
    assert agg["estimated_item_count"] == 0
    assert agg["max_total_score"] == 55
    assert agg["estimated_total_score"] == 0


# ---------------------------------------------------------------------------
# 一致性裁剪 + 总分可靠性信号(防"预估评标得分"超100/估分>满分)
# ---------------------------------------------------------------------------


def test_aggregate_clamps_estimate_over_item_max():
    # 单项预估超过本项满分必须被裁剪(防图示 972/194 反常)
    agg = compute_scoring_aggregate([_si(10, 50, "technical")])
    assert agg["estimated_total_score"] == 10
    assert agg["estimated_score_by_volume"]["技术标"]["estimated"] == 10


def test_aggregate_volume_estimate_not_exceed_volume_max():
    # 满分缺失项(0/None)致单项 clamp 失守时, 卷级兜底确保预估不破卷满分
    agg = compute_scoring_aggregate(
        [_si(60, 40, "technical"), _si(0, 30, "technical")]
    )
    tech = agg["estimated_score_by_volume"]["技术标"]
    assert tech["max"] == 60
    assert tech["estimated"] <= tech["max"]


def test_aggregate_reliable_when_total_near_100():
    # 单一100分制: 满分合计≈100 → 可信, 展示统一总分
    agg = compute_scoring_aggregate(
        [_si(60, 50, "technical"), _si(40, 30, "commercial")]
    )
    assert agg["max_total_score"] == 100
    assert agg["scoring_total_reliable"] is True


def test_aggregate_unreliable_when_total_exceeds_100():
    # 多卷/非100制(图示 88.4/180、1076/300): 满分合计远超100 → 不可信, 前端降级
    agg = compute_scoring_aggregate(
        [_si(160, 88, "technical"), _si(106, 60, "commercial")]
    )
    assert agg["max_total_score"] == 266
    assert agg["scoring_total_reliable"] is False


def test_aggregate_unreliable_when_empty():
    agg = compute_scoring_aggregate([])
    assert agg["scoring_total_reliable"] is False


def test_aggregate_excludes_no_score_items_from_estimate():
    # 实测(项目1496): 无分值项(满分None/0)被 LLM 赋 estimated=100 不应污染预估;
    # 否则卷级 clamp 会把污染掩盖成"满分/满分"全满分假象。
    items = [
        _si(10, 8, "technical"),
        _si(None, 100, "qualification"),   # 无分值资格说明项, est 应被排除
        _si(0, 100, "other"),               # 满分0(格式说明项), est 应被排除
    ]
    agg = compute_scoring_aggregate(items)
    assert agg["scoring_item_count"] == 3   # 仍计为评分项
    assert agg["estimated_item_count"] == 1  # 仅 1 项有效满分项计入预估
    assert agg["estimated_total_score"] == 8
    assert agg["max_total_score"] == 10
    tech = agg["estimated_score_by_volume"]["技术标"]
    assert tech["estimated"] == 8 and tech["estimated"] <= tech["max"]


# ---------------------------------------------------------------------------
# 第2层: 按卷加权折算到统一100分制
# ---------------------------------------------------------------------------


def test_aggregate_weighted_fold_to_unified_100():
    # 技术40%+价格60%加权制: 技术率80%×0.4 + 价格率60%×0.6 = 32+36 = 68, 满分对标100
    items = [
        _si(100, 80, "technical", volume_weight=0.4, volume_max=100),
        _si(100, 60, "price", volume_weight=0.6, volume_max=100),
    ]
    agg = compute_scoring_aggregate(items)
    assert agg["scoring_total_reliable"] is True
    assert agg["estimated_total_score"] == 68
    assert agg["max_total_score"] == 100
    assert agg["estimated_score_by_volume"]["技术标"]["weight"] == 0.4
    assert agg["estimated_score_by_volume"]["价格"]["weight"] == 0.6


def test_aggregate_weighted_fold_degrades_when_weight_not_cover_all():
    # 仅技术卷有权重、其余卷无权重(项目1496 实况) → 无法折算 → 降级仅按卷参考
    items = [
        _si(100, 80, "technical", volume_weight=0.4, volume_max=100),
        _si(11, 5, "qualification"),
    ]
    agg = compute_scoring_aggregate(items)
    assert agg["scoring_total_reliable"] is False
    assert agg["max_total_score"] == 111  # 未折算, 维持原始满分合计


# ---------------------------------------------------------------------------
# 满分全集口径(纳入 reconcile 回填到 normal 主行的重点评审项)
# ---------------------------------------------------------------------------


def test_universe_member_scoring_item_always_in():
    # scoring_item 通道无条件计入(即使无满分, 与既有 missing 统计口径一致)
    assert is_scoring_universe_member(
        requirement_kind="scoring_item", is_key_review=0, max_score=None
    ) is True
    assert is_scoring_universe_member(
        requirement_kind="scoring_item", is_key_review=0, max_score=0
    ) is True


def test_universe_member_normal_key_with_score_in():
    # reconcile 回填: normal 主行带 is_key_review + 有效满分 → 计入
    assert is_scoring_universe_member(
        requirement_kind="normal", is_key_review=1, max_score=19
    ) is True


def test_universe_member_normal_key_without_score_excluded():
    # 仅 is_key_review 无满分(relevance 标记但无 scoring_item 回填) → 不计入(防0污染/防双计)
    assert is_scoring_universe_member(
        requirement_kind="normal", is_key_review=1, max_score=None
    ) is False
    assert is_scoring_universe_member(
        requirement_kind="normal", is_key_review=1, max_score=0
    ) is False


def test_universe_member_plain_normal_excluded():
    assert is_scoring_universe_member(
        requirement_kind="normal", is_key_review=0, max_score=10
    ) is False


def _normal_key(max_score, est, volume):
    """reconcile 回填到 normal 主行的重点评审项(requirement_kind=normal, is_key_review=1)。"""
    return {
        "requirement_kind": "normal",
        "is_key_review": True,
        "max_score": max_score,
        "estimated_score": est,
        "volume": volume,
        "volume_weight": None,
        "volume_max": None,
    }


def test_aggregate_includes_reconciled_normal_main_rows():
    """ 真实场景(项目1499 技术卷): scoring_item 60 + normal 主行回填(业绩19+专利3) = 82, 而非 60。"""
    items = [
        _si(60, 50, "technical"),          # scoring_item 技术合计(简化为单条)
        _normal_key(19, 15, "technical"),  # 业绩19 已回填到 normal 主行
        _normal_key(3, 2, "technical"),    # 专利3 已回填到 normal 主行
    ]
    agg = compute_scoring_aggregate(items)
    tech = agg["estimated_score_by_volume"]["技术标"]
    assert tech["max"] == 82            # 60 + 19 + 3, 修复前仅 60
    assert tech["estimated"] == 67      # 50 + 15 + 2
    assert agg["scoring_item_count"] == 3
    assert agg["max_total_score"] == 82


def test_aggregate_excludes_keyreview_normal_without_score():
    """仅 is_key_review 无满分的 normal 主行不计入(防双计/防0污染)。"""
    items = [
        _si(60, 50, "technical"),
        _normal_key(None, None, "technical"),  # 无满分 → 排除
    ]
    agg = compute_scoring_aggregate(items)
    assert agg["scoring_item_count"] == 1
    assert agg["estimated_score_by_volume"]["技术标"]["max"] == 60


# ---------------------------------------------------------------------------
# 多采购包按包聚合(项目1554 实况: 两包各100分制被混算成 200 误判"结构复杂")
# ---------------------------------------------------------------------------


def _lot_si(max_score, est, volume, lot):
    return {**_si(max_score, est, volume), "lot": lot}


def test_aggregate_multi_lot_groups_per_lot():
    """项目1554 实况: 包1(技术70+价格30) 包2(技术90+价格10) 各自独立百分制。

    修复前: 两包混算 max_total=200 → scoring_total_reliable=False →
    前端错误播报"评分结构较复杂(含多卷/加权/非100分制)", 与确认页按包核计矛盾。
    """
    items = [
        _lot_si(70, 0, "technical", "采购包1"),
        _lot_si(30, 30, "price", "采购包1"),
        _lot_si(90, 6.5, "technical", "采购包2"),
        _lot_si(10, 0, "price", "采购包2"),
    ]
    agg = compute_scoring_aggregate(items)
    by_lot = agg["estimated_score_by_lot"]
    assert set(by_lot.keys()) == {"采购包1", "采购包2"}
    lot1, lot2 = by_lot["采购包1"], by_lot["采购包2"]
    # 每包独立套用单一100分制判定 → 各自可信, 不再被跨包混算污染
    assert lot1["reliable"] is True and lot1["max"] == 100 and lot1["estimated"] == 30
    assert lot2["reliable"] is True and lot2["max"] == 100 and lot2["estimated"] == 6.5
    assert lot1["by_volume"]["技术标"]["max"] == 70
    assert lot2["by_volume"]["技术标"]["max"] == 90
    # 顶层禁止展示统一总分(100+100=200 无评标语义)
    assert agg["scoring_total_reliable"] is False
    assert agg["max_total_score"] == 200
    # by_volume 复合键"包·卷", 供结果页评分卷导航按包细分
    assert "采购包1·技术标" in agg["estimated_score_by_volume"]
    assert "采购包2·价格" in agg["estimated_score_by_volume"]
    assert agg["scoring_item_count"] == 4


def test_aggregate_multi_lot_unassigned_fail_visible():
    """ 教训: lot 缺失评分项禁止静默丢弃/并入任何包, 须独立成"未识别归属"桶。"""
    items = [
        _lot_si(100, 80, "technical", "采购包1"),
        _lot_si(100, 60, "technical", "采购包2"),
        _si(5, 3, "technical"),  # lot 缺失
    ]
    agg = compute_scoring_aggregate(items)
    by_lot = agg["estimated_score_by_lot"]
    assert "未识别归属" in by_lot
    bucket = by_lot["未识别归属"]
    assert bucket["unassigned"] is True
    assert bucket["max"] == 5 and bucket["count"] == 1
    # 未归属分值不得混入任何真实包
    assert by_lot["采购包1"]["max"] == 100
    assert by_lot["采购包2"]["max"] == 100


def test_aggregate_single_lot_keeps_flat_behavior():
    """单包文档(lot 全同/全缺失)不进入分包模式, 旧行为零变化(回归护栏)。"""
    same_lot = [
        _lot_si(60, 50, "technical", "采购包1"),
        _lot_si(40, 30, "commercial", "采购包1"),
    ]
    agg = compute_scoring_aggregate(same_lot)
    assert agg["estimated_score_by_lot"] == {}
    assert agg["scoring_total_reliable"] is True
    assert agg["max_total_score"] == 100
    assert "技术标" in agg["estimated_score_by_volume"]  # 键不带包前缀

    no_lot = [_si(60, 50, "technical"), _si(40, 30, "commercial")]
    agg2 = compute_scoring_aggregate(no_lot)
    assert agg2["estimated_score_by_lot"] == {}
    assert agg2["scoring_total_reliable"] is True


def test_aggregate_multi_lot_unreliable_lot_degrades_per_lot():
    """某包结构复杂(非100制)只降级该包, 不拖累其他包的可信展示。"""
    items = [
        _lot_si(70, 50, "technical", "采购包1"),
        _lot_si(30, 20, "price", "采购包1"),
        _lot_si(150, 90, "technical", "采购包2"),  # 包2 合计150 ≠ 100
    ]
    agg = compute_scoring_aggregate(items)
    by_lot = agg["estimated_score_by_lot"]
    assert by_lot["采购包1"]["reliable"] is True
    assert by_lot["采购包2"]["reliable"] is False


# ---------------------------------------------------------------------------
# 多采购包按包独立核计(项目1554: 两包各100分制被混算成200判"结构复杂")
# ---------------------------------------------------------------------------


def _lot_si(max_score, est, volume, lot):
    return {**_si(max_score, est, volume), "lot": lot}


def test_aggregate_multi_lot_groups_per_lot_project_1554():
    """项目1554实况: 包1(技术70+价格30) + 包2(技术90+价格10), 各包独立100分制。

    修复前: 两包混算 max_total=200 → scoring_total_reliable=False → 前端误报
    "评分结构较复杂(含多卷/加权/非100分制)", 与确认页按包核计通过的结论矛盾。
    """
    items = [
        _lot_si(70, 0, "technical", "采购包1"),
        _lot_si(30, 30, "price", "采购包1"),
        _lot_si(90, 6.5, "technical", "采购包2"),
        _lot_si(10, 0, "price", "采购包2"),
    ]
    agg = compute_scoring_aggregate(items)
    by_lot = agg["estimated_score_by_lot"]
    assert set(by_lot) == {"采购包1", "采购包2"}
    lot1, lot2 = by_lot["采购包1"], by_lot["采购包2"]
    # 每包独立满足单一100分制 → 包级可信, 按包展示预估
    assert lot1["max"] == 100 and lot1["reliable"] is True and lot1["estimated"] == 30
    assert lot2["max"] == 100 and lot2["reliable"] is True and lot2["estimated"] == 6.5
    assert lot1["count"] == 2 and lot2["count"] == 2
    assert lot1["unassigned"] is False
    # 包内按卷明细保留(前端行内展示"技术标 0/70、价格 30/30")
    assert lot1["by_volume"]["技术标"]["max"] == 70
    assert lot1["by_volume"]["价格"]["estimated"] == 30
    # 跨包不得合成统一总分(100+100=200 无评标语义)
    assert agg["scoring_total_reliable"] is False
    assert agg["max_total_score"] == 200
    # 评分卷导航键按"包·卷"细分, 与明细行 lot+volume 构造一致
    assert "采购包1·技术标" in agg["estimated_score_by_volume"]
    assert "采购包2·价格" in agg["estimated_score_by_volume"]
    assert agg["estimated_score_by_volume"]["采购包1·技术标"]["max"] == 70


def test_aggregate_multi_lot_unassigned_fail_visible():
    """lot 缺失条目必须进"未识别归属"桶(教训: 分组聚合禁止静默丢弃)。"""
    items = [
        _lot_si(70, 50, "technical", "采购包1"),
        _lot_si(30, 20, "price", "采购包1"),
        _lot_si(100, 60, "technical", "采购包2"),
        _si(5.5, 3, "technical"),  # lot=None(如归并链路丢失)
    ]
    agg = compute_scoring_aggregate(items)
    by_lot = agg["estimated_score_by_lot"]
    assert "未识别归属" in by_lot
    bucket = by_lot["未识别归属"]
    assert bucket["unassigned"] is True
    assert bucket["max"] == 5.5 and bucket["count"] == 1
    # 未归属分值不得并入任何真实包
    assert by_lot["采购包1"]["max"] == 100
    assert by_lot["采购包2"]["max"] == 100
    assert "未识别归属·技术标" in agg["estimated_score_by_volume"]


def test_aggregate_multi_lot_per_lot_reliability_independent():
    """包级可靠性独立判定: 一包100分制可信, 另一包结构异常仅该包降级。"""
    items = [
        _lot_si(60, 40, "technical", "采购包1"),
        _lot_si(40, 30, "price", "采购包1"),
        _lot_si(160, 88, "technical", "采购包2"),  # 该包非100制
    ]
    agg = compute_scoring_aggregate(items)
    by_lot = agg["estimated_score_by_lot"]
    assert by_lot["采购包1"]["reliable"] is True
    assert by_lot["采购包2"]["reliable"] is False


def test_aggregate_single_lot_keeps_flat_behavior():
    """单包文档(lot 全同/全缺)不进入分包模式, 行为与历史完全一致(回归护栏)。"""
    same_lot = [
        _lot_si(60, 50, "technical", "采购包1"),
        _lot_si(40, 30, "commercial", "采购包1"),
    ]
    agg = compute_scoring_aggregate(same_lot)
    assert agg["estimated_score_by_lot"] == {}
    assert agg["scoring_total_reliable"] is True
    assert agg["max_total_score"] == 100
    assert set(agg["estimated_score_by_volume"]) == {"技术标", "商务标"}

    no_lot = [_si(60, 50, "technical"), _si(40, 30, "commercial")]
    agg2 = compute_scoring_aggregate(no_lot)
    assert agg2["estimated_score_by_lot"] == {}
    assert agg2["scoring_total_reliable"] is True


# ---------------------------------------------------------------------------
# _resolve_estimated_score (clamp + 无响应=0 + 非评分项 None)
# ---------------------------------------------------------------------------


def _pair(kind="scoring_item", max_score=10.0, is_key=0):
    return SimpleNamespace(
        requirement=SimpleNamespace(
            requirement_kind=kind, score_weight=max_score, is_key_review=is_key
        )
    )


def _extracted(est, comment=""):
    return SimpleNamespace(estimated_score=est, comment=comment)


def test_resolve_clamps_to_max():
    val = _resolve_estimated_score(_extracted(11), _pair(max_score=10.0), ComplianceLevel.FULLY)
    assert val == 10.0


def test_resolve_clamps_negative_to_zero():
    val = _resolve_estimated_score(_extracted(-3), _pair(max_score=10.0), ComplianceLevel.PARTIALLY)
    assert val == 0.0


def test_resolve_missing_response_is_zero():
    val = _resolve_estimated_score(_extracted(None), _pair(), ComplianceLevel.MISSING)
    assert val == 0.0


def test_resolve_not_compliant_falls_back_to_zero():
    # 复发修正: 评分项判"不符合"且 LLM 漏填、意见无数字 -> 不得分=0(而非 None)
    val = _resolve_estimated_score(_extracted(None), _pair(), ComplianceLevel.NOT)
    assert val == 0.0


def test_resolve_none_for_normal_non_key_requirement():
    # 普通要求且非重点评审 -> 不预估
    val = _resolve_estimated_score(_extracted(8), _pair(kind="normal", is_key=0), ComplianceLevel.FULLY)
    assert val is None


def test_resolve_covers_is_key_review_normal_requirement():
    # 重点评审的普通要求(非 scoring_item)也应预估, 与 Prompt/读取口径一致(根因B)
    val = _resolve_estimated_score(
        _extracted(None, comment="判定为不符合，得分0分。"),
        _pair(kind="normal", max_score=6.0, is_key=1),
        ComplianceLevel.NOT,
    )
    assert val == 0.0


def test_resolve_passthrough_within_range():
    val = _resolve_estimated_score(_extracted(7.5), _pair(max_score=15.0), ComplianceLevel.PARTIALLY)
    assert val == 7.5


# ---------------------------------------------------------------------------
# compute_estimated_score (统一口径: 落库/读取/汇总共用)
# ---------------------------------------------------------------------------


def test_compute_priority_structured_first_and_clamp():
    assert compute_estimated_score(
        structured_value=12, comment="得分3分", max_score=10.0,
        compliance_level="partially", applies=True,
    ) == 10.0


def test_compute_comment_fallback_when_structured_missing():
    assert compute_estimated_score(
        structured_value=None, comment="故判定为不符合，得分0分。", max_score=6.0,
        compliance_level="not", applies=True,
    ) == 0.0


def test_compute_not_missing_zero_without_text_signal():
    assert compute_estimated_score(
        structured_value=None, comment="不符合要求。", max_score=10.0,
        compliance_level="not", applies=True,
    ) == 0.0
    assert compute_estimated_score(
        structured_value=None, comment="", max_score=10.0,
        compliance_level=ComplianceLevel.MISSING, applies=True,
    ) == 0.0


def test_compute_partially_without_signal_is_none():
    # 部分符合但无任何得分信号 -> 不武断, None
    assert compute_estimated_score(
        structured_value=None, comment="部分满足要求。", max_score=10.0,
        compliance_level="partially", applies=True,
    ) is None


def test_compute_not_applicable_is_none():
    assert compute_estimated_score(
        structured_value=5, comment="得分5分", max_score=10.0,
        compliance_level="fully", applies=False,
    ) is None
