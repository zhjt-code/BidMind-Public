"""S1.2 防御测试: 共享评分核计报告 build_totals_report_payload。

映射风险: 审查确认页与写标核计横幅共用同一 payload 构建函数, 口径若漂移会导致
两侧"是否需人工复核"结论不一致(同 教训)。锁定关键判定:
-: 价格卷无子项(仅技术卷=100/100)不因权重和≠100%误报缺口。
-: 无卷定义(单一制)时回退全局满分校验, 不漏报合计≠100。
- 空集返回 None; 缺分值项触发复核。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from app.core.extractor.scoring.score_totals import (  # noqa: E402
    ScoringTotalsEntry,
    build_totals_report_payload,
)
from app.core.extractor.scoring.scoring_facts import build_scoring_fact_set  # noqa: E402
from app.core.extractor.scoring.scoring_scope_validator import (  # noqa: E402
    ScoringValidationKind,
    validate_scoring_fact_set,
)
from app.schemas.requirement import ExtractedScoringItem  # noqa: E402

pytestmark = pytest.mark.scoring


def _scoring_item(name: str, score: float, lot: str | None = None, volume: str | None = "technical"):
    return ExtractedScoringItem(
        category="TECH",
        score_item_name=name,
        description=f"{name} 满分{score:g}分",
        score_weight=score,
        lot=lot,
        volume=volume,
        score_rules=[f"{name}最高得{score:g}分"],
    )


def test_empty_entries_returns_none():
    assert build_totals_report_payload([]) is None


def test_scope_validator_accepts_two_lots_each_100():
    fact_set = build_scoring_fact_set([
        _scoring_item("技术项", 70, "采购包1"),
        _scoring_item("报价评分", 30, "采购包1", "price"),
        _scoring_item("技术项", 90, "采购包2"),
        _scoring_item("报价评分", 10, "采购包2", "price"),
    ])

    validation = validate_scoring_fact_set(fact_set)

    assert validation.kind == ScoringValidationKind.COMPLETE
    assert validation.needs_review is False
    assert validation.totals_by_lot == {"采购包1": 100.0, "采购包2": 100.0}


def test_scope_validator_flags_single_lot_gap():
    fact_set = build_scoring_fact_set([
        _scoring_item("技术项", 84, "采购包2"),
        _scoring_item("报价评分", 10, "采购包2", "price"),
    ])

    validation = validate_scoring_fact_set(fact_set)

    assert validation.kind == ScoringValidationKind.EVIDENCE_GAP
    assert validation.needs_review is True
    assert "采购包2" in validation.note


def test_scope_validator_flags_unassigned_lot():
    fact_set = build_scoring_fact_set([
        _scoring_item("技术项", 100, "__unassigned__"),
    ])

    validation = validate_scoring_fact_set(fact_set)

    assert validation.kind == ScoringValidationKind.SCOPE_CONFLICT
    assert validation.needs_review is True


def test_single_100_global_pass():
    # 单一100分制(无卷权重): 合计=100 校验通过, 不需复核
    entries = [
        ScoringTotalsEntry(score_weight=60.0),
        ScoringTotalsEntry(score_weight=40.0),
    ]
    payload = build_totals_report_payload(entries)
    assert payload is not None
    assert payload["needs_review"] is False
    assert payload["item_count"] == 2


def test_missing_rule_count_flags_short_scoring_item_description():
    # P0 A3: 有有效分值但 score_rules/deduction_rules 为空，且描述仅为评分项名/极短文本时，
    # 应显式提示评审标准疑似缺失，避免只靠分值合计通过掩盖规则缺口。
    entries = [
        ScoringTotalsEntry(score_weight=100.0, has_rules=False, description_length=4),
    ]
    payload = build_totals_report_payload(entries)

    assert payload is not None
    assert payload["missing_rule_count"] == 1
    assert payload["needs_review"] is True
    assert any("评审标准疑似缺失" in d for d in payload["diagnostics"])


def test_missing_rule_diagnostic_names_the_affected_scoring_item():
    """: 真缺规则时必须给出项名，不能只报数量让用户逐项盲查。"""
    entries = [
        ScoringTotalsEntry(
            score_weight=30.0,
            has_rules=False,
            description_length=4,
            diagnostic_name="价格评审",
        ),
        ScoringTotalsEntry(
            score_weight=70.0,
            has_rules=True,
            description_length=30,
            diagnostic_name="技术评审",
        ),
    ]

    payload = build_totals_report_payload(entries)

    assert payload is not None
    assert payload["missing_rule_count"] == 1
    assert any("价格评审" in diagnostic for diagnostic in payload["diagnostics"])


def test_missing_rule_count_flags_none_description_as_missing():
    # description=None 比短描述更危险，应按无评审标准处理，而非因 None 被静默豁免。
    entries = [
        ScoringTotalsEntry(score_weight=100.0, has_rules=False, description_length=0),
    ]
    payload = build_totals_report_payload(entries)

    assert payload is not None
    assert payload["missing_rule_count"] == 1
    assert payload["needs_review"] is True


def test_missing_rule_count_lot_and_unassigned_entries_accumulate():
    # 多包 + 未归属路径下，missing_rule_count 必须跨子报告与未归属项累加。
    entries = [
        ScoringTotalsEntry(score_weight=100.0, lot="采购包1", has_rules=False, description_length=4),
        ScoringTotalsEntry(score_weight=100.0, lot="采购包2", has_rules=True, description_length=30),
        ScoringTotalsEntry(score_weight=5.0, has_rules=False, description_length=2),
    ]
    payload = build_totals_report_payload(entries)

    assert payload is not None
    assert payload["missing_rule_count"] == 2
    assert payload["needs_review"] is True
    diag = "\n".join(payload["diagnostics"])
    assert "采购包1" in diag
    assert "未归属项" in diag


def test_scoring_source_counts_authority_and_llm_entries():
    entries = [
        ScoringTotalsEntry(score_weight=60.0, is_llm_source=False),
        ScoringTotalsEntry(score_weight=40.0, is_llm_source=True),
    ]
    payload = build_totals_report_payload(entries)

    assert payload is not None
    assert payload["authority_count"] == 1
    assert payload["llm_count"] == 1


def test_scoring_source_counts_across_lots_and_unassigned():
    entries = [
        ScoringTotalsEntry(score_weight=100.0, lot="采购包1", is_llm_source=False),
        ScoringTotalsEntry(score_weight=100.0, lot="采购包2", is_llm_source=True),
        ScoringTotalsEntry(score_weight=5.0, is_llm_source=True),
    ]
    payload = build_totals_report_payload(entries)

    assert payload is not None
    assert payload["authority_count"] == 1
    assert payload["llm_count"] == 2


def test_bug541_no_volume_defs_undershoot_flagged():
    # 有卷标识但无卷权重/卷满分 → volume_defs 为空 → 回退全局校验;
    # 技术65 + 价格30 = 95 < 100, 必须判需复核(不能被按卷校验的空 volume_checks 漏报)。
    entries = [
        ScoringTotalsEntry(score_weight=65.0, volume="technical"),
        ScoringTotalsEntry(score_weight=30.0, volume="price"),
    ]
    payload = build_totals_report_payload(entries)
    assert payload is not None
    assert payload["needs_review"] is True
    assert payload["volume_checks"] == {}


def test_bug511_price_volume_no_subitems_not_flagged():
    # 技术卷子项满分和=卷满分(100/100)且无缺失时, 即便只识别到技术卷(权重和≠100%),
    # 也不应因 weight_sum_ok=False 触发"评分项缺口"复核。
    entries = [
        ScoringTotalsEntry(score_weight=70.0, volume="technical", volume_weight=0.4, volume_max=100.0),
        ScoringTotalsEntry(score_weight=30.0, volume="technical", volume_weight=0.4, volume_max=100.0),
    ]
    payload = build_totals_report_payload(entries)
    assert payload is not None
    assert payload["weight_sum_ok"] is False  # 只看到技术卷40%权重
    assert payload["needs_review"] is False  # 但技术卷100/100达标 → 不误报


def test_by_volume_gap_flagged():
    # 技术卷子项和 82 ≠ 卷满分 100 → 按卷校验判缺口, 需复核
    entries = [
        ScoringTotalsEntry(score_weight=82.0, volume="technical", volume_weight=1.0, volume_max=100.0),
    ]
    payload = build_totals_report_payload(entries)
    assert payload is not None
    assert payload["needs_review"] is True
    # diagnostics 面向用户展示, 不得泄露内部英文卷标识"technical", 须为中文卷名。
    assert any("技术标" in d for d in payload["diagnostics"])
    assert not any("technical" in d for d in payload["diagnostics"])
    # 低于基准(82<100) → 归因方向必须是"漏提", 不得说"重复提取"。
    assert any("漏提" in d for d in payload["diagnostics"])
    assert not any("重复提取" in d for d in payload["diagnostics"])


def test_extra_parse_diagnostics_are_shown_only_when_needs_review():
    """解析层 fail-visible 诊断只在核计异常时并入横幅, 避免健康表产生噪声。"""
    warning = "组'技术部分'声明48分，表内可解析28分，缺口20分"

    failed_payload = build_totals_report_payload(
        [ScoringTotalsEntry(score_weight=80.0)],
        extra_diagnostics=[warning, warning],
    )
    assert failed_payload is not None
    assert failed_payload["needs_review"] is True
    assert failed_payload["diagnostics"].count(warning) == 1

    passed_payload = build_totals_report_payload(
        [ScoringTotalsEntry(score_weight=100.0)],
        extra_diagnostics=[warning],
    )
    assert passed_payload is not None
    assert passed_payload["needs_review"] is False
    assert warning not in passed_payload["diagnostics"]


def test_bug551_over_basis_diagnostics_says_duplicate_not_missing():
    """(项目1539真实回归): 技术卷因假评分项多计到103≠100, 归因必须指向
 "重复提取/识别错误", 不能沿用"漏识别"——超出基准的卷不可能是"漏"。
 """
    entries = [
        ScoringTotalsEntry(score_weight=103.0, volume="technical", volume_weight=1.0, volume_max=100.0),
    ]
    payload = build_totals_report_payload(entries)
    assert payload is not None
    assert payload["needs_review"] is True
    diag = "".join(payload["diagnostics"])
    assert "technical" not in diag
    assert "技术标" in diag
    assert "重复提取" in diag
    assert "漏提" not in diag


def test_volume_label_and_gap_direction_helpers():
    """: 中文卷名映射与偏差方向归因是独立可测的纯函数。"""
    from app.core.extractor.scoring.score_totals import (
        _gap_direction_phrase,
        _volume_label,
    )

    assert _volume_label("technical") == "技术标"
    assert _volume_label("commercial") == "商务标"
    # 未知卷标识原样回退, 不静默丢弃信息
    assert _volume_label("some_unknown_volume") == "some_unknown_volume"

    assert "重复提取" in _gap_direction_phrase(103.0, 100.0)
    assert "漏提" not in _gap_direction_phrase(103.0, 100.0)
    assert "漏提" in _gap_direction_phrase(82.0, 100.0)
    assert "重复提取" not in _gap_direction_phrase(82.0, 100.0)


def test_bug554_partial_volume_defs_diagnostics_include_global_context():
    """(项目1547): volume_checks 仅覆盖部分有分卷(商务技术有定义/价格无)时,
 "已识别18/应为20"缺全局上下文会被误读为"满分是20"——diagnostics 必须补全局合计。
 """
    entries = [
        ScoringTotalsEntry(score_weight=18.0, volume="technical", volume_weight=0.2, volume_max=20.0),
        ScoringTotalsEntry(score_weight=80.0, volume="price"),
    ]
    payload = build_totals_report_payload(entries)
    assert payload is not None
    assert payload["needs_review"] is True
    diag = "".join(payload["diagnostics"])
    assert "全局满分合计" in diag
    assert "98" in diag


def test_bug554_full_volume_defs_no_redundant_global_line():
    """卷定义已覆盖全部有分卷时不追加全局行, 避免通过态外的冗余噪音。"""
    entries = [
        ScoringTotalsEntry(score_weight=82.0, volume="technical", volume_weight=1.0, volume_max=100.0),
    ]
    payload = build_totals_report_payload(entries)
    assert payload is not None
    assert payload["needs_review"] is True
    assert not any("全局满分合计" in d for d in payload["diagnostics"])


def test_bug555_needs_review_diagnostics_include_volume_composition_pages():
    """: 横幅 needs_review 时应给用户可核对的按卷构成明细与来源页。

 测试数据更新（遗留清理）: 原双卷各100场景在独立百分制识别落地后属合法通过态，
 改用"技术100+商务60"真不闭合场景保留本测试的 needs_review 诊断断言意图。
 """
    entries = [
        ScoringTotalsEntry(score_weight=100.0, volume="technical", source_page=21),
        ScoringTotalsEntry(score_weight=60.0, volume="commercial", source_page=26),
    ]
    payload = build_totals_report_payload(entries)

    assert payload is not None
    assert payload["needs_review"] is True
    diag = "\n".join(payload["diagnostics"])
    assert "已识别构成" in diag
    assert "技术标 100.0 分（第21页）" in diag
    assert "商务标 60.0 分（第26页）" in diag


def test_bug555_pass_diagnostics_do_not_include_volume_composition_noise():
    """通过态不追加构成明细, 避免绿色横幅冗余。"""
    entries = [
        ScoringTotalsEntry(score_weight=100.0, volume="technical", volume_weight=1.0, volume_max=100.0, source_page=21),
    ]
    payload = build_totals_report_payload(entries)

    assert payload is not None
    assert payload["needs_review"] is False
    assert not any("已识别构成" in d for d in payload["diagnostics"])


def test_bug556_two_lots_each_100_pass_independently():
    """: 多采购包文档按 lot 独立100基准校验, 不能合并成200/100。"""
    entries = [
        ScoringTotalsEntry(score_weight=70.0, volume="technical", lot="采购包1", source_page=36),
        ScoringTotalsEntry(score_weight=30.0, volume="price", lot="采购包1", source_page=34),
        ScoringTotalsEntry(score_weight=90.0, volume="technical", lot="采购包2", source_page=41),
        ScoringTotalsEntry(score_weight=10.0, volume="price", lot="采购包2", source_page=40),
    ]
    payload = build_totals_report_payload(entries)

    assert payload is not None
    assert payload["needs_review"] is False
    assert payload["by_lot"] == {"采购包1": 100.0, "采购包2": 100.0}
    assert "采购包1" in payload["note"] and "采购包2" in payload["note"]


def test_bug556_lot_gap_reports_specific_package():
    """: 多包场景缺口应精确落到对应采购包, 不再笼统报全局合计。"""
    entries = [
        ScoringTotalsEntry(score_weight=70.0, volume="technical", lot="采购包1", source_page=36),
        ScoringTotalsEntry(score_weight=30.0, volume="price", lot="采购包1", source_page=34),
        ScoringTotalsEntry(score_weight=90.0, volume="technical", lot="采购包2", source_page=41),
    ]
    payload = build_totals_report_payload(entries)

    assert payload is not None
    assert payload["needs_review"] is True
    diag = "\n".join(payload["diagnostics"])
    assert "采购包2" in diag
    assert "90.0" in diag
    assert "100.0" in diag


def test_scoring_composition_strips_score_suffix_from_volume_label_bug631():
    """: 构成摘要中卷名自带分值时不得重复展示“技术部分（14分） 14分”。"""
    entries = [
        ScoringTotalsEntry(
            score_weight=70.0,
            volume="price",
            volume_label="报价部分",
            source_page=11,
            has_rules=False,
            description_length=4,
        ),
        ScoringTotalsEntry(score_weight=16.0, volume="commercial", volume_label="商务部分（16分）", source_page=12),
        ScoringTotalsEntry(score_weight=14.0, volume="technical", volume_label="技术部分（14分）", source_page=12),
    ]
    payload = build_totals_report_payload(entries, extra_diagnostics=["触发构成展示"])

    assert payload is not None
    diagnostics = "\n".join(payload["diagnostics"])
    assert "已识别构成（合计100.0分）" in diagnostics
    assert "商务部分 16.0 分（第12页）" in diagnostics
    assert "技术部分 14.0 分（第12页）" in diagnostics
    assert "商务部分（16分） 16.0" not in diagnostics


def test_bug557_unassigned_lot_entries_not_silently_dropped():
    """(项目1553真实回归): reconcile 回填丢 lot 后 3 项(5.5分)落 lot=None,
 旧 lot 分组把它们静默丢弃 → 包1 被误报 94.5"漏提"。未归属条目必须计入全集并显式提示。
 """
    entries = [
        ScoringTotalsEntry(score_weight=64.5, volume="technical", lot="采购包1", source_page=36),
        ScoringTotalsEntry(score_weight=30.0, volume="price", lot="采购包1", source_page=34),
        ScoringTotalsEntry(score_weight=90.0, volume="technical", lot="采购包2", source_page=41),
        ScoringTotalsEntry(score_weight=10.0, volume="price", lot="采购包2", source_page=40),
        # reconcile 回填行丢失 lot 的形态
        ScoringTotalsEntry(score_weight=1.5, volume="technical", source_page=36),
        ScoringTotalsEntry(score_weight=2.0, volume="technical", source_page=36),
        ScoringTotalsEntry(score_weight=2.0, volume="technical", source_page=36),
    ]
    payload = build_totals_report_payload(entries)

    assert payload is not None
    assert payload["needs_review"] is True
    assert payload["item_count"] == 7  # 未归属 3 项不得从全集蒸发
    diag = "\n".join(payload["diagnostics"])
    assert "未识别采购包归属" in diag
    assert "5.5" in diag
    assert "未归属项已识别构成" in diag
    # 全局按卷构成包含未归属分值(154.5+5.5=160.0)
    assert payload["by_volume"]["technical"] == 160.0


def test_bug557_lot_diagnostics_no_duplicate_lines():
    """: 单包子报告全局回退路径 note 与 diagnostics[0] 同文,
 外层 lot 循环曾同时追加两者 → 同一句告警在横幅重复两遍。"""
    entries = [
        ScoringTotalsEntry(score_weight=64.5, volume="technical", lot="采购包1", source_page=36),
        ScoringTotalsEntry(score_weight=30.0, volume="price", lot="采购包1", source_page=34),
        ScoringTotalsEntry(score_weight=100.0, volume="technical", lot="采购包2", source_page=41),
    ]
    payload = build_totals_report_payload(entries)

    assert payload is not None
    assert payload["needs_review"] is True
    diags = payload["diagnostics"]
    assert len(diags) == len(set(diags)), f"diagnostics 存在重复行: {diags}"


def test_bug557_multi_lot_overview_shows_passing_lot():
    """: 通过的包必须在概览中可见, 否则用户会把"横幅未提及包2"误读为"包2没提取到"。"""
    entries = [
        ScoringTotalsEntry(score_weight=94.5, volume="technical", lot="采购包1", source_page=36),
        ScoringTotalsEntry(score_weight=100.0, volume="technical", lot="采购包2", source_page=41),
    ]
    payload = build_totals_report_payload(entries)

    assert payload is not None
    assert payload["needs_review"] is True
    overview = payload["diagnostics"][0]
    assert "各采购包核计" in overview
    assert "采购包1 94.5/100.0（未通过）" in overview
    assert "采购包2 100.0/100.0（通过）" in overview


def test_bug557_multi_lot_pass_no_unassigned_no_overview_noise():
    """双包各100且无未归属项 → 通过态, 不追加概览/未归属噪音, note 含各包合计。"""
    entries = [
        ScoringTotalsEntry(score_weight=70.0, volume="technical", lot="采购包1", source_page=36),
        ScoringTotalsEntry(score_weight=30.0, volume="price", lot="采购包1", source_page=34),
        ScoringTotalsEntry(score_weight=90.0, volume="technical", lot="采购包2", source_page=41),
        ScoringTotalsEntry(score_weight=10.0, volume="price", lot="采购包2", source_page=40),
    ]
    payload = build_totals_report_payload(entries)

    assert payload is not None
    assert payload["needs_review"] is False
    assert payload["diagnostics"] == []
    assert payload["by_lot"] == {"采购包1": 100.0, "采购包2": 100.0}
    assert "采购包1 100.0/100.0" in payload["note"]
    assert "采购包2 100.0/100.0" in payload["note"]


def test_missing_score_flags_review():
    # 含 0 分值项(未识别到满分)计入 missing_score_count → 需复核
    entries = [
        ScoringTotalsEntry(score_weight=100.0),
        ScoringTotalsEntry(score_weight=0.0),
    ]
    payload = build_totals_report_payload(entries)
    assert payload is not None
    assert payload["missing_score_count"] == 1
    assert payload["needs_review"] is True


def test_bug550_flat_scheme_weight_only_def_not_flagged():
    # (项目1536): 最终得分制——技术6项合计60(无卷定义) + 商务1项40分,
    # 商务卷带"权重40%"型定义(volume_max 被 parser 兜底猜 100)。
    # 全局合计=100(平铺制强证据) 且 商务合计40=权重0.4×100 → 不应误报"40≠100 漏识别"。
    entries = [
        ScoringTotalsEntry(score_weight=10.0, volume="technical"),
        ScoringTotalsEntry(score_weight=12.0, volume="technical"),
        ScoringTotalsEntry(score_weight=15.0, volume="technical"),
        ScoringTotalsEntry(score_weight=15.0, volume="technical"),
        ScoringTotalsEntry(score_weight=6.0, volume="technical"),
        ScoringTotalsEntry(score_weight=2.0, volume="technical"),
        ScoringTotalsEntry(
            score_weight=40.0, volume="commercial", volume_weight=0.4, volume_max=100.0
        ),
    ]
    payload = build_totals_report_payload(entries)
    assert payload is not None
    assert payload["needs_review"] is False
    check = payload["volume_checks"]["commercial"]
    assert check["within"] is True
    # 最终得分制命中时按其基准(40)展示, 不再出现"40/100"误导
    assert check["expected"] == 40.0


def test_bug550_true_gap_still_flagged_in_flat_scheme():
    # 真缺口防回归: 商务只提到25分(权重0.4应为40), 全局合计85≠100 → 两基准均不匹配, 必须标记
    entries = [
        ScoringTotalsEntry(score_weight=60.0, volume="technical"),
        ScoringTotalsEntry(
            score_weight=25.0, volume="commercial", volume_weight=0.4, volume_max=100.0
        ),
    ]
    payload = build_totals_report_payload(entries)
    assert payload is not None
    assert payload["needs_review"] is True
    assert payload["volume_checks"]["commercial"]["within"] is False


def test_bug550_folded_scheme_regression_unchanged():
    # 经典折算制防回归: 各卷合计=卷基准100、全局合计200≠100 →
    # 平铺分支不启用, 纯折算基准判定, 通过态不变
    entries = [
        ScoringTotalsEntry(
            score_weight=100.0, volume="technical", volume_weight=0.4, volume_max=100.0
        ),
        ScoringTotalsEntry(
            score_weight=100.0, volume="price", volume_weight=0.6, volume_max=100.0
        ),
    ]
    payload = build_totals_report_payload(entries)
    assert payload is not None
    assert payload["needs_review"] is False
    assert payload["volume_checks"]["technical"]["expected"] == 100.0


def test_bug601_three_weighted_100_volumes_not_flagged_as_300_overflow():
    # (项目1574): 资信/技术/商务各卷内满分100, 按10%/50%/40%折算到最终100。
    # by_volume 合计为300是卷内满分集合, 只要各卷100/100且权重和100%, 确认页不应误报。
    entries = [
        ScoringTotalsEntry(
            score_weight=100.0, volume="qualification", volume_weight=0.1, volume_max=100.0
        ),
        ScoringTotalsEntry(
            score_weight=100.0, volume="technical", volume_weight=0.5, volume_max=100.0
        ),
        ScoringTotalsEntry(
            score_weight=100.0, volume="commercial", volume_weight=0.4, volume_max=100.0
        ),
    ]
    payload = build_totals_report_payload(entries)
    assert payload is not None
    assert payload["needs_review"] is False
    assert payload["weight_sum_ok"] is True
    assert payload["by_volume"] == {
        "qualification": 100.0,
        "technical": 100.0,
        "commercial": 100.0,
    }
    assert all(check["within"] for check in payload["volume_checks"].values())


def test_bug550_folded_gap_not_masked_by_flat_coincidence():
    # 掩盖防护: 折算制技术卷仅提到40分(应100), 全局合计40≠100 → 平铺分支被全局证据禁用,
    # 不得因 40=权重0.4×100 的巧合被误放行
    entries = [
        ScoringTotalsEntry(
            score_weight=40.0, volume="technical", volume_weight=0.4, volume_max=100.0
        ),
    ]
    payload = build_totals_report_payload(entries)
    assert payload is not None
    assert payload["needs_review"] is True
    assert payload["volume_checks"]["technical"]["within"] is False


# ---------------------------------------------------------------------------
# 结构丢失信号强制门禁测试
# ---------------------------------------------------------------------------

_PARTIAL_REJECTION_DIAG = "物理表部分段验收失败: pages=1-2 scored_rows=3"


def test_bug798_single_scope_100_still_needs_review_when_structural_signal():
    """单 scope 总分=100 通过，但 extra_diagnostics 含结构丢失信号 → needs_review=True + 诊断可见。"""
    from app.core.extractor.scoring.score_totals import build_totals_report_payload

    entries = [
        _make_entry("技术方案", 70.0),
        _make_entry("投标报价", 30.0, volume="price"),
    ]
    payload = build_totals_report_payload(entries, extra_diagnostics=[_PARTIAL_REJECTION_DIAG])
    assert payload is not None
    assert payload["needs_review"] is True, "结构丢失信号应强制 needs_review=True"
    assert payload["structure_review_required"] is True
    assert any("物理表部分段验收失败" in d for d in payload["diagnostics"]), (
        "extra_diagnostics 中的结构丢失信号必须出现在最终 diagnostics"
    )


def test_bug798_single_scope_100_no_signal_passes_normally():
    """单 scope 总分=100，无结构丢失信号 → needs_review=False（保持既有降噪语义）。"""
    from app.core.extractor.scoring.score_totals import build_totals_report_payload

    entries = [
        _make_entry("技术方案", 70.0),
        _make_entry("投标报价", 30.0, volume="price"),
    ]
    payload = build_totals_report_payload(entries, extra_diagnostics=["普通数值诊断提示A"])
    assert payload is not None
    assert payload["needs_review"] is False, "无结构信号时 100 分不应触发 needs_review"
    assert payload["structure_review_required"] is False
    assert "普通数值诊断提示A" not in payload["diagnostics"], (
        "needs_review=False 时普通诊断不得进入 diagnostics（既有降噪语义）"
    )


def test_bug798_multi_lot_each_100_still_needs_review_when_structural_signal():
    """多 lot 每包均=100 通过，extra_diagnostics 含结构丢失信号 → 整体 needs_review=True + 诊断可见。"""
    from app.core.extractor.scoring.score_totals import build_totals_report_payload

    entries = [
        _make_entry("技术方案A", 70.0, lot="A包"),
        _make_entry("投标报价A", 30.0, lot="A包", volume="price"),
        _make_entry("技术方案B", 70.0, lot="B包"),
        _make_entry("投标报价B", 30.0, lot="B包", volume="price"),
    ]
    payload = build_totals_report_payload(entries, extra_diagnostics=[_PARTIAL_REJECTION_DIAG])
    assert payload is not None
    assert payload["needs_review"] is True, "各包数值通过但有结构信号，整体应 needs_review=True"
    assert payload["structure_review_required"] is True
    assert any("物理表部分段验收失败" in d for d in payload["diagnostics"]), (
        "结构丢失信号必须出现在最终 diagnostics"
    )


def test_bug798_structural_signal_neutral_note():
    """数值通过但结构信号触发 → note 使用中性措辞。"""
    from app.core.extractor.scoring.score_totals import build_totals_report_payload

    entries = [_make_entry("技术项", 100.0)]
    payload = build_totals_report_payload(entries, extra_diagnostics=[_PARTIAL_REJECTION_DIAG])
    assert payload is not None
    assert "未能完整验收" in payload["note"], (
        f"结构门禁触发时 note 应为中性措辞，实际: {payload['note']}"
    )


def test_bug798_diagnostics_require_review_policy():
    """diagnostics_require_review 只对结构丢失信号返回 True。"""
    from app.core.extractor.scoring.scoring_diagnostic_policy import diagnostics_require_review

    assert diagnostics_require_review([_PARTIAL_REJECTION_DIAG]) is True
    assert diagnostics_require_review(["普通诊断A", "数值偏差B"]) is False
    assert diagnostics_require_review([]) is False
    assert diagnostics_require_review(None) is False
    assert diagnostics_require_review(["非结构信号，物理表部分 other"]) is False, (
        "仅含结构信号子串的普通诊断不得触发（需以完整信号短语为前缀）"
    )


def test_structure_review_required_defaults_false_at_all_totals_exits():
    """单包、lot、scheme 三类 payload 均显式输出默认 false。"""
    from app.core.extractor.scoring.score_totals import build_totals_report_payload

    single = build_totals_report_payload([_make_entry("单包项", 100.0)])
    lots = build_totals_report_payload([
        _make_entry("A包项", 100.0, lot="A包"),
        _make_entry("B包项", 100.0, lot="B包"),
    ])
    schemes = build_totals_report_payload([
        ScoringTotalsEntry(score_weight=100.0, scheme="scheme_1"),
        ScoringTotalsEntry(score_weight=100.0, scheme="scheme_2"),
    ])

    assert single is not None and single["structure_review_required"] is False
    assert lots is not None and lots["structure_review_required"] is False
    assert schemes is not None and schemes["structure_review_required"] is False


def test_structure_review_required_propagates_through_scheme_exit():
    """多方案聚合必须消费同一结构诊断策略并输出 true。"""
    from app.core.extractor.scoring.score_totals import build_totals_report_payload

    payload = build_totals_report_payload(
        [
            ScoringTotalsEntry(score_weight=100.0, scheme="scheme_1"),
            ScoringTotalsEntry(score_weight=100.0, scheme="scheme_2"),
        ],
        extra_diagnostics=[_PARTIAL_REJECTION_DIAG],
    )

    assert payload is not None
    assert payload["needs_review"] is True
    assert payload["structure_review_required"] is True


def _make_entry(
    name: str,
    score: float,
    lot: str | None = None,
    volume: str = "technical",
) -> ScoringTotalsEntry:
    """构造测试用 ScoringTotalsEntry。"""
    return ScoringTotalsEntry(
        score_weight=score,
        volume=volume,
        diagnostic_name=name,
        lot=lot,
    )
