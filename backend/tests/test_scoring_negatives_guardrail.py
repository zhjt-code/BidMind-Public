"""评分项提取 5 类负例护栏测试（P1-3）。

职责: 验证已知误提取反模式能被正确拒绝；未实现能力以 strict xfail 标记目标，
      reason 指向 P3 内核。
不做: 不调用 LLM/DB、不读取 PDF/fixture、不测试前端展示。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.extractor.scoring.scoring_facts import build_scoring_fact_set  # noqa: E402
from app.core.extractor.scoring.scoring_scope_validator import (
    validate_scoring_fact_set,  # noqa: E402
)

pytestmark = pytest.mark.scoring


def _make_item(
    name: str,
    score: float,
    *,
    volume: str = "unspecified",
    pages: list[int] | None = None,
    fact_type: str = "base_score",
    volume_max: float | None = None,
) -> SimpleNamespace:
    """构造测试用评分项 SimpleNamespace。"""
    return SimpleNamespace(
        score_item_name=name,
        score_weight=score,
        volume=volume,
        scheme="",
        lot="",
        source_pages=pages if pages is not None else [1],
        fact_type=fact_type,
        description=name,
        volume_weight=None,
        volume_max=volume_max,
        score_rules=[],
        deduction_rules=[],
    )


# ---------------------------------------------------------------------------
# 1. 说明列作名称
# ---------------------------------------------------------------------------

@pytest.mark.xfail(
    strict=True,
    reason=(
        "P3 kernel (scoring_item_naming): description-as-name 检测能力未实现；"
        "当前 total=100 闭合时校验器不检查名称是否为规则描述文本"
    ),
)
def test_description_as_name_flagged() -> None:
    """说明列误作名称: score_item_name 含完整评分规则描述时应触发 needs_review。

    负例根因: 表格解析将规则/说明列误认为名称列，产生 100+ 字符的伪名称，
    闭合通过但项名可读性与语义均不符合评分项命名规范。
    """
    rule_text = (
        "根据投标人提供的施工质量保障措施全面性、针对性、合理性评分；"
        "内容全面合理针对性强得[2,3]；较全面较合理较有针对性得[1,2)；"
        "不够全面/一般得[0,1)"
    )
    items = [_make_item(rule_text, 70), _make_item("报价评分", 30)]
    fact_set = build_scoring_fact_set(items, source_id="test:neg1")
    validation = validate_scoring_fact_set(fact_set)
    # 期望：名称为规则文本应触发审核，当前不触发（total=100 直接通过）
    assert validation.needs_review is True, f"description-as-name should need review; got: {validation}"


# ---------------------------------------------------------------------------
# 2. 错卷凑100（无声明卷基准）
# ---------------------------------------------------------------------------

@pytest.mark.xfail(
    strict=True,
    reason=(
        "P3 kernel: 无声明卷基准时两卷分配错误但合计=100，"
        "确定性链校验器通过全局总和 fallback（优先级3）判定为闭合，无法识别卷级伪闭合"
    ),
)
def test_wrong_volume_distribution_without_defs_not_rejected() -> None:
    """错卷凑100(无声明): 两卷分布错误但总分恰好=100，校验器应识别为伪闭合。

    负例根因: 无 volume_max 声明时，validate_scoring_fact_set 退化为全局总和 fallback，
    不检查各卷独立闭合情况，导致 technical=60(实际应50)+commercial=40(实际应50)通过。
    """
    items = [
        _make_item("技术方案", 60, volume="technical"),
        _make_item("商务报价", 40, volume="commercial"),
    ]
    fact_set = build_scoring_fact_set(items, source_id="test:neg2")
    validation = validate_scoring_fact_set(fact_set)
    # 期望：各卷分配错误应触发审核，当前 total=100 直接通过
    assert validation.needs_review is True, f"wrong distribution should need review; got: {validation}"


# ---------------------------------------------------------------------------
# 3. 同物理证据重复（当前已实现去重，应通过）
# ---------------------------------------------------------------------------

def test_duplicate_same_page_evidence_deduplicated() -> None:
    """同物理证据重复: 相同页码、名称、分值的重复评分项应被 fact_key 去重。

    根因防御: build_scoring_fact_set 用 scheme|lot|volume|page|name|score 做键；
    两个完全相同的输入项应归并为一条有效事实，total 不双计。
    """
    items = [
        _make_item("技术方案", 70, pages=[5]),
        _make_item("技术方案", 70, pages=[5]),  # exact duplicate
        _make_item("报价评分", 30, pages=[6]),
    ]
    fact_set = build_scoring_fact_set(items, source_id="test:neg3")
    assert len(fact_set.effective_facts) == 2, (
        f"duplicate must be deduplicated; got {len(fact_set.effective_facts)} facts"
    )
    assert fact_set.total_score == 100.0, f"total should be 100; got {fact_set.total_score}"


# ---------------------------------------------------------------------------
# 4. 父子双计
# ---------------------------------------------------------------------------

@pytest.mark.xfail(
    strict=True,
    reason=(
        "P3 kernel: parent-child double-counting detection not implemented；"
        "父项(50)+子项合计(50)=100 时校验器判定为闭合通过，不识别层级重叠"
    ),
)
def test_parent_child_double_count_not_detected() -> None:
    """父子双计: 父项(50)+子项合计(50)=100，校验器应识别为双重计分。

    负例根因: 上层汇总行（父）与明细行（子）同时出现时，total 被双计。
    期望校验器通过层级关系检测识别此形态并触发 needs_review。
    """
    items = [
        _make_item("技术部分", 50, volume="technical"),   # parent: should not be counted with children
        _make_item("方案评分", 30, volume="technical"),   # child-1
        _make_item("人员评分", 20, volume="technical"),   # child-2
    ]
    fact_set = build_scoring_fact_set(items, source_id="test:neg4")
    # 当前 total=100（父+子），全局 fallback 判定通过
    assert fact_set.total_score == 100.0, "parent+children sum should be 100 in current chain"
    validation = validate_scoring_fact_set(fact_set)
    assert validation.needs_review is True, f"parent-child double count should need review; got: {validation}"


# ---------------------------------------------------------------------------
# 5. 无证据闭合
# ---------------------------------------------------------------------------

@pytest.mark.xfail(
    strict=True,
    reason=(
        "P3 kernel: physical evidence (source_pages) required for closure not enforced；"
        "source_pages 为空的评分项当前仍可通过闭合校验，无物理证据锚定的闭合不可信"
    ),
)
def test_no_source_pages_closure_not_blocked() -> None:
    """无证据闭合: source_pages 为空的评分项不应被允许直接通过闭合校验。

    负例根因: 校验器不检查 source_pages，只校验分值之和；
    无来源页意味着提取缺乏 PDF 物理证据，闭合结论不可信。
    """
    items = [
        _make_item("技术方案", 70, pages=[]),   # no physical evidence
        _make_item("报价评分", 30, pages=[]),   # no physical evidence
    ]
    fact_set = build_scoring_fact_set(items, source_id="test:neg5")
    validation = validate_scoring_fact_set(fact_set)
    # 期望：无来源页时不应通过闭合，当前 total=100 直接通过
    assert validation.needs_review is True, f"items without source_pages should need review; got: {validation}"
