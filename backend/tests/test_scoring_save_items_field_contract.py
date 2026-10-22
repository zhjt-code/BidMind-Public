"""评分项落库字段映射契约（治理阶段 1.3）。

职责: 机械证明 ExtractedScoringItem 的**每一个**字段都进入持久化表示，
      从而使 T0 快照的推论「快照零差分 ⇒ 落库零漂移」成立。
不做: 不连数据库、不断言业务取值是否正确、不校验 SQL 语句。

为什么需要它:
  T0 快照锁定的是 collect_scoring_structure 的产物（内存 items）。
  若某字段虽在产物中却从未落库，快照零差分就无法推出用户可见结果零漂移；
  反之若新增字段忘记落库，快照也不会报红。本测试封住这个推论缺口。

覆盖判据（两条并集，须覆盖 ExtractedScoringItem 全部字段）:
  1. 经 build_scoring_item_metadata 进入 scoring_metadata；
  2. 在 ScoringExtractor._save_items 内被直接读取用于构造 Requirement 列。
"""
from __future__ import annotations

import ast
import os
import pathlib

import pytest

pytestmark = pytest.mark.scoring

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-key-not-for-prod")
os.environ.setdefault("ADMIN_PASSWORD", "<TEST_PASSWORD>")

from app.core.extractor.scoring.scoring_item_metadata import (  # noqa: E402
    build_scoring_item_metadata,
)
from app.schemas.requirement import ExtractedScoringItem  # noqa: E402

EXTRACTOR_SOURCE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "app/core/extractor/scoring/scoring_extractor.py"
)


def _sample_item() -> ExtractedScoringItem:
    """全字段取值互不相同，便于逐字段核对映射未串位。"""
    return ExtractedScoringItem(
        category="TECH",
        category_basis="keyword:技术方案",
        score_item_name="施工组织设计",
        description="按施工组织设计的完整性与可行性评分",
        score_weight=12.5,
        volume="technical",
        volume_label="技术标",
        lot="采购包1",
        scheme="scheme_2",
        fact_type="bonus",
        volume_weight=0.4,
        volume_max=60.0,
        score_rules=["优秀得12.5分", "良好得8分"],
        deduction_rules=["缺项每处扣2分"],
        source_pages=[17, 18],
    )


def _save_items_function() -> ast.AsyncFunctionDef:
    tree = ast.parse(EXTRACTOR_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_save_items":
            return node
    pytest.fail("未找到 ScoringExtractor._save_items")


def _fields_read_in_save_items() -> set[str]:
    """AST 提取 _save_items 内对循环变量 `it` 的属性读取。"""
    func = _save_items_function()
    fields: set[str] = set()
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "it"
        ):
            fields.add(node.attr)
    return fields


class TestScoringItemPersistenceFieldContract:
    """保证提取产物到落库表示的字段覆盖不留缺口。"""

    def test_metadata_builder_carries_expected_values(self) -> None:
        """metadata 构建点逐字段取值正确且未串位。"""
        item = _sample_item()
        meta = build_scoring_item_metadata(
            item, effective_weight=12.5, totals_needs_review=True
        )
        expected = {
            "score_item_name": "施工组织设计",
            "max_score": 12.5,
            "score_rules": ["优秀得12.5分", "良好得8分"],
            "deduction_rules": ["缺项每处扣2分"],
            "category_basis": "keyword:技术方案",
            "volume": "technical",
            "volume_label": "技术标",
            "lot": "采购包1",
            "scheme": "scheme_2",
            "fact_type": "bonus",
            "volume_weight": 0.4,
            "volume_max": 60.0,
            "totals_needs_review": True,
        }
        assert meta == expected

    def test_every_item_field_reaches_persistence(self) -> None:
        """ExtractedScoringItem 每个字段都必须落库（metadata 或 Requirement 列）。

        新增字段却忘记落库时本测试失败——否则 T0 快照会锁住一个
        「产物里有、用户看不到」的字段，零倒退推论出现缺口。
        """
        item = _sample_item()
        meta_keys = set(
            build_scoring_item_metadata(
                item, effective_weight=item.score_weight, totals_needs_review=False
            )
        )
        # metadata 中的键名与 item 字段名不完全同名，此处做显式对齐
        meta_covered = meta_keys | {"score_weight"} if "max_score" in meta_keys else meta_keys
        covered = meta_covered | _fields_read_in_save_items()

        all_fields = set(ExtractedScoringItem.model_fields)
        missing = sorted(all_fields - covered)
        assert not missing, (
            "以下 ExtractedScoringItem 字段既未进入 scoring_metadata，"
            f"也未被 _save_items 读取，落库将丢失: {missing}"
        )

    # 阶段3(2026-08-31): T0 快照仪器(scripts/scoring_snapshot.py)已随确定性链
    # 退役, 对应的快照字段清单交叉校验测试同步移除; 落库字段契约由本类其余
    # 用例(schema→scoring_metadata→_save_items 三方对账)继续护栏。
