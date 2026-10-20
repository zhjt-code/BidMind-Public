""": 测试 OCR 噪声分值格的证据恢复功能。

覆盖点:
- Unit: recover_explicit_row_score 的 4 道门禁 + 语义边界
- Unit: 新增负例 (档次带/父子项冲突/扣减语境/最高加/满分冲突)
- Unit: 最高可得N分正向识别 vs 最高加N分负向识别
- Integration: project_2497 fixture 修复后 7 项 / 100 分
- Integration: 供货实施方案独立存在, volume == "technical"
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path bootstrap (resolves backend root for imports)
# ---------------------------------------------------------------------------

_BACKEND = Path(__file__).parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.core.extractor.scoring.scoring_row_evidence_recovery import (  # noqa: E402
    RowRecoveryContext,
    attempt_row_recovery,
    recover_explicit_row_score,
)
from app.core.extractor.scoring.scoring_table_parser import parse_scoring_table  # noqa: E402

pytestmark = pytest.mark.scoring

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_FIXTURE_DIR = (
    Path(__file__).parent / "fixtures" / "scoring" / "bug798"
)


def _load_2497_grid() -> dict:
    """返回 chunk 子字典（含 grid / column_roles / row_pages）。"""
    raw = json.loads((_FIXTURE_DIR / "project_2497_grid.json").read_text(encoding="utf-8"))
    return raw["chunk"]


# ---------------------------------------------------------------------------
# Unit tests — recover_explicit_row_score
# ---------------------------------------------------------------------------


class TestNoiseRecoveryUnit:
    """Gate-level unit tests for recover_explicit_row_score."""

    # --- Positive: real noise cell from project 2497 ---

    def test_noise_cell_recovery_returns_15(self):
        """The real garbled fixture cell must recover to 15 with ≥2 evidence types."""
        result = recover_explicit_row_score(
            name="供货实施方案",
            score_cell="e 5 2 4 156分 2 8 d",
            rule_text=(
                "每项内容各占3 分，共15分。"
                " 有针对性且完全满足项目需求得15分，"
                " 基本满足得10分，不满足得0分。"
            ),
        )
        assert result is not None
        assert result.score == 15.0
        # Must have at least 2 distinct evidence types, one of which is cumulative
        assert len(result.evidence_types) >= 2
        assert len(set(result.evidence_types)) >= 2
        assert "cumulative_total" in result.evidence_types

    # --- Negative: Gate 1 (name) ---

    def test_empty_score_cell_returns_none(self):
        """Empty score cell must pass Gate 2; empty name fails Gate 1."""
        assert recover_explicit_row_score(
            name="", score_cell="", rule_text="共15分，满分15分"
        ) is None

    # --- Negative: Gate 2 (score cell qualification) ---

    def test_normal_parseable_score_cell_returns_none(self):
        """Normal parseable cell (12分) must not be intercepted."""
        assert recover_explicit_row_score(
            name="商务响应",
            score_cell="12分",
            rule_text="共12分，满分12分",
        ) is None

    # --- Negative: Gate 3 (conflicting evidence) ---

    def test_conflicting_cumulative_and_full_score_returns_none(self):
        """共12分 (Type B) vs 得15分 (Type C) must conflict and return None."""
        assert recover_explicit_row_score(
            name="技术方案",
            score_cell="a b 1 2 c",
            rule_text="每项内容完全满足项目需求得15分，共12分",
        ) is None

    # --- Negative: deduction-only rule ---

    def test_deduction_only_rule_returns_none(self):
        """纯扣分规则不提供整项得分证据，应返回 None。"""
        assert recover_explicit_row_score(
            name="响应文件",
            score_cell="z 3 q 2 x",
            rule_text="每缺少一项扣3分，每缺少一条扣2分，共扣完为止",
        ) is None

    # --- Negative: price formula with multiple numbers ---

    def test_price_formula_returns_none(self):
        """价格公式 (A÷B×C×100×0.4) 中的多个数字不得被解读为整项得分。"""
        assert recover_explicit_row_score(
            name="报价得分",
            score_cell="0 4 x 1 0 0",
            rule_text="供应商报价得分=最低报价÷供应商报价×100×0.4，共40分",
        ) is None

    # ---------------------------------------------------------------------------
    # New negative tests — Critical review requirements
    # ---------------------------------------------------------------------------

    def test_grade_band_rule_returns_none(self):
        """档次带（多档得分）无累计总分声明，不满足两类独立证据要求。"""
        assert recover_explicit_row_score(
            name="技术方案评审",
            score_cell="a b 2 z",
            rule_text=(
                "90-100分得15分；80-89分得12分；60-79分得8分；60分以下得0分"
            ),
        ) is None

    def test_parent_item_with_nested_children_returns_none(self):
        """父项共14分，内嵌子项最高4/2分，Type B=14 与 Type C=4 冲突 → None。"""
        assert recover_explicit_row_score(
            name="供货计划",
            score_cell="x 1 4 y",
            rule_text=(
                "共14分：子项A最高可得4分，子项B最高可得2分，其余按比例分配"
            ),
        ) is None

    def test_deduction_cumulative_plus_full_satisfy_returns_none(self):
        """减共15分（扣减语境）不计入Type B；Type C唯一 → 证据类别不足 → None。"""
        assert recover_explicit_row_score(
            name="供货实施方案",
            score_cell="a z 1 5 b",
            rule_text=(
                "累计减共15分，各项内容完全满足项目需求得15分"
            ),
        ) is None

    def test_two_same_tier_scores_no_cumulative_returns_none(self):
        """两个相同档次得15分但无总声明；Type B缺失 → 证据类别=1 → None。"""
        assert recover_explicit_row_score(
            name="技术服务方案",
            score_cell="b 1 5 c",
            rule_text=(
                "全部满足需求得15分；大部分满足需求得10分；基本满足得5分"
            ),
        ) is None

    def test_highest_plus_score_does_not_match(self):
        """最高加N分不匹配 full_highest 模式，无Type B → None。"""
        assert recover_explicit_row_score(
            name="加分项",
            score_cell="x 1 0 y",
            rule_text="每项最高加10分，共6项",
        ) is None

    def test_multiple_full_score_conflict_returns_none(self):
        """满分15分 (Type C=15) vs 累计得分共12分 (Type B=12) 冲突 → None。"""
        assert recover_explicit_row_score(
            name="综合评分",
            score_cell="z 1 5 a",
            rule_text="满分15分，累计得分共12分",
        ) is None

    # ---------------------------------------------------------------------------
    # New positive test — 最高可得N分
    # ---------------------------------------------------------------------------

    def test_max_achievable_score_is_recognized(self):
        """最高可得N分作为 full_highest 证据，配合 累计总分 应恢复正确值。"""
        result = recover_explicit_row_score(
            name="项目实施方案",
            score_cell="a b 1 5 c",
            rule_text="共15分，各项内容最高可得15分",
        )
        assert result is not None
        assert result.score == 15.0
        assert "full_highest" in result.evidence_types
        assert "cumulative_total" in result.evidence_types

    def test_highest_add_score_is_excluded_from_full_highest(self):
        """最高加N分不应匹配 full_highest；与总声明共10分也不足 → None。"""
        assert recover_explicit_row_score(
            name="加分事项",
            score_cell="z 1 0 y",
            rule_text="最高加10分，共10分",
        ) is None

    # ---------------------------------------------------------------------------
    # Deduction context window tests (原 lookbehind 升级为窗口语义判定)
    # ---------------------------------------------------------------------------

    def test_kou_fen_gong_is_deduction_context(self):
        """'扣分共N分' 是扣减语境，不得成为整项总分证据。"""
        assert recover_explicit_row_score(
            name="响应评估",
            score_cell="a 1 5 b",
            rule_text="扣分共15分，满分15分",
        ) is None

    def test_kou_jian_gong_is_deduction_context(self):
        """'扣减共N分' 是扣减语境，不得成为整项总分证据。"""
        assert recover_explicit_row_score(
            name="响应评估",
            score_cell="a 1 5 b",
            rule_text="扣减共15分，满分15分",
        ) is None

    def test_jian_gong_is_deduction_context(self):
        """'减共N分' 是扣减语境，不得成为整项总分证据。"""
        assert recover_explicit_row_score(
            name="响应评估",
            score_cell="a 1 5 b",
            rule_text="减共15分，满分15分",
        ) is None

    def test_lei_ji_kou_chu_gong_is_deduction_context(self):
        """'累计扣除共N分' 是扣减语境，不得成为整项总分证据。"""
        assert recover_explicit_row_score(
            name="响应评估",
            score_cell="a 1 5 b",
            rule_text="累计扣除共15分，满分15分",
        ) is None

    def test_zong_gong_is_positive_cumulative(self):
        """'总共N分' 是正向累计证据，与第二类证据一致时应成功恢复。"""
        result = recover_explicit_row_score(
            name="技术方案",
            score_cell="x y 1 5 z",
            rule_text="总共15分，有针对性且完全满足项目需求得15分",
        )
        assert result is not None
        assert result.score == 15.0
        assert "cumulative_total" in result.evidence_types


# ---------------------------------------------------------------------------
# Volume inference behavior tests (via attempt_row_recovery, not private helpers)
# ---------------------------------------------------------------------------

# Shared noise cell / rule that always recover to produce an item
_NOISE_CELL = "e 5 2 4 156分 2 8 d"
_RULE_15 = (
    "每项内容各占3分，共15分。"
    "有针对性且完全满足项目需求得15分，基本满足得10分，不满足得0分。"
)


def _ctx(last_cat: str, current_volume: str = "") -> RowRecoveryContext:
    """Build a minimal RowRecoveryContext for volume-inference behavior tests."""
    return RowRecoveryContext(
        fallback_name="供货实施方案",
        score_cell=_NOISE_CELL,
        rule_text=_RULE_15,
        content_text="",
        last_cat=last_cat,
        current_volume=current_volume,
        volume_label=None,
        row_page=None,
    )


class TestVolumeInferenceBehavior:
    """Verify that attempt_row_recovery derives the correct volume via
    volume_from_scoring_label when current_volume is empty.

    Covers: 商务资质→commercial, 技术服务→technical, 无关→''.
    Uses public attempt_row_recovery; private helper is not tested directly.
    """

    def test_commercial_category_infers_commercial(self):
        """'商务资质' 类别应推断 volume='commercial'。"""
        item = attempt_row_recovery(_ctx("商务资质"))
        assert item is not None
        assert item.volume == "commercial"

    def test_technical_category_infers_technical(self):
        """'技术服务部分' 类别应推断 volume='technical'（技术优先于服务）。"""
        item = attempt_row_recovery(_ctx("技术服务部分"))
        assert item is not None
        assert item.volume == "technical"

    def test_unrelated_category_infers_empty(self):
        """与 volume 无关的类别文本应返回 volume=''，不产生误判。"""
        item = attempt_row_recovery(_ctx("评审项目"))
        assert item is not None
        assert item.volume == ""

    def test_explicit_volume_takes_precedence_over_category(self):
        """current_volume 有值时，不应被 last_cat 覆盖。"""
        ctx = _ctx("商务资质", current_volume="price")
        item = attempt_row_recovery(ctx)
        assert item is not None
        assert item.volume == "price"


# ---------------------------------------------------------------------------
# Integration tests — project 2497 fixture
# ---------------------------------------------------------------------------


class TestProject2497Integration:
    """Integration: parse_scoring_table must correctly recover the garbled row."""

    @pytest.fixture(scope="class")
    def grid_payload(self):
        return _load_2497_grid()

    @pytest.fixture(scope="class")
    def parsed_result(self, grid_payload):
        return parse_scoring_table(
            markdown="",
            grid=grid_payload["grid"],
            column_roles=grid_payload.get("column_roles"),
            row_pages=grid_payload.get("row_pages"),
        )

    def test_item_count_and_total_after_fix(self, parsed_result):
        """修复后应有 7 个评分项、总分 100.0。"""
        items = parsed_result.items
        assert len(items) == 7, (
            f"期望 7 项，实际 {len(items)} 项: "
            + str([(i.score_item_name, i.score_weight) for i in items])
        )
        total = sum(i.score_weight for i in items)
        assert total == 100.0, f"期望总分 100.0，实际 {total}"

    def test_supply_plan_exists_and_is_technical(self, parsed_result):
        """供货实施方案应独立存在且 volume == 'technical'（不应并入商务项）。"""
        items = parsed_result.items
        matching = [i for i in items if "供货实施方案" in i.score_item_name]
        assert matching, "未找到包含'供货实施方案'的评分项"

        supply_item = matching[0]
        assert supply_item.score_weight == 15.0, (
            f"期望分值 15.0，实际 {supply_item.score_weight}"
        )
        assert supply_item.volume == "technical", (
            f"期望 volume='technical'，实际 '{supply_item.volume}'"
        )
