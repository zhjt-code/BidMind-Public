"""pdf_direct 主路径防御测试（2026-08-31 用户裁决: 直传PDF为唯一评分提取通道）。

覆盖: PDF闭合强证据(volume_closure)正常/边界/异常路径、verdict 闭合豁免、
      JSONL 宽容解析回退(美化JSON/数组包装)、模式分叉与扫描件兜底次序、
      direct_primary 输入不可用的 fail-visible 空产物。
不做: 不调真实 LLM/DB、不跑 legacy 确定性链(存量测试职责)。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.extractor.scoring.ledger.contracts import (
    BasisLevel,
    LedgerReport,
    LedgerStatus,
)
from app.core.extractor.scoring.score_totals import ScoringTotalsEntry
from app.core.extractor.scoring.scoring_green_verdict import (
    VERDICT_AMBER,
    VERDICT_GREEN,
    resolve_scoring_verdict,
)
from app.core.extractor.scoring.vlm.candidate_contract import extract_jsonl_records
from app.core.extractor.scoring.volume_closure import build_volume_closure_payload

pytestmark = pytest.mark.scoring


# ---------------------------------------------------------------------------
# PDF闭合强证据 volume_closure
# ---------------------------------------------------------------------------

def _entry(**overrides) -> ScoringTotalsEntry:
    base = dict(
        score_weight=10.0,
        volume="technical",
        volume_label="技术部分",
        volume_weight=None,
        volume_max=100.0,
        has_rules=True,
        diagnostic_name="某评分项",
    )
    base.update(overrides)
    return ScoringTotalsEntry(**base)


def _doc3479_entries() -> list[ScoringTotalsEntry]:
    """项目3479真实形态: 报价100(w0.3) + 商务(60+40, w0.1) + 技术9项100(w0.6)。"""
    entries = [
        _entry(score_weight=100, volume="price", volume_label="报价部分",
               volume_weight=0.3, diagnostic_name="响应报价"),
        _entry(score_weight=60, volume="commercial", volume_label="商务部分",
               volume_weight=0.1, diagnostic_name="承包商业绩"),
        _entry(score_weight=40, volume="commercial", volume_label="商务部分",
               volume_weight=0.1, diagnostic_name="文件编写质量"),
    ]
    tech = [("项目管理机构设置", 10), ("团队人数", 4), ("职称配备", 6),
            ("服务方案编制", 13), ("技术路线", 12), ("进度计划", 15),
            ("质量保障措施", 15), ("保密与知识产权", 15), ("后续技术支持", 10)]
    entries.extend(
        _entry(score_weight=w, volume_weight=0.6, diagnostic_name=name)
        for name, w in tech
    )
    return entries


class TestVolumeClosure:
    def test_doc3479_three_volumes_closed_verified(self):
        payload = build_volume_closure_payload(_doc3479_entries())
        assert payload is not None and payload["verified"] is True
        assert len(payload["groups"]) == 3

    def test_commercial_gap_40_not_verified(self):
        """3479 事故形态: 商务只提到业绩60, 缺文件编写40 → 不判绿。"""
        entries = [e for e in _doc3479_entries() if e.diagnostic_name != "文件编写质量"]
        payload = build_volume_closure_payload(entries)
        assert payload["verified"] is False
        assert payload["reason"] == "group_gap"

    def test_whole_volume_omitted_with_weights_not_verified(self):
        """整卷漏提(仅报价100/卷闭合)但权重0.3≠1 → 权重探测器拦截。"""
        entries = [_entry(score_weight=100, volume="price", volume_label="报价部分",
                          volume_weight=0.3, diagnostic_name="响应报价")]
        payload = build_volume_closure_payload(entries)
        assert payload["verified"] is False
        assert payload["reason"] in ("weight_sum", "weight_partial")

    def test_no_weights_two_volumes_closed_verified(self):
        entries = [
            _entry(score_weight=60, volume="technical", volume_label="技术标",
                   volume_max=60, diagnostic_name="方案"),
            _entry(score_weight=40, volume="commercial", volume_label="商务标",
                   volume_max=40, diagnostic_name="业绩"),
        ]
        assert build_volume_closure_payload(entries)["verified"] is True

    def test_duplicate_triple_not_verified(self):
        entries = [
            _entry(score_weight=50, volume_max=100, diagnostic_name="重复项"),
            _entry(score_weight=50, volume_max=100, diagnostic_name="重复项"),
        ]
        payload = build_volume_closure_payload(entries)
        assert payload["verified"] is False and payload["reason"] == "duplicate"

    def test_unlabeled_item_not_verified(self):
        entries = _doc3479_entries()
        entries.append(_entry(volume=None, volume_label=None, volume_max=None,
                              diagnostic_name="游离项"))
        assert build_volume_closure_payload(entries)["reason"] == "unlabeled"

    def test_missing_score_not_verified(self):
        entries = [_entry(score_weight=0, diagnostic_name="无分值项"),
                   _entry(score_weight=100, diagnostic_name="正常项")]
        assert build_volume_closure_payload(entries)["reason"] == "missing_score"

    def test_multi_lot_returns_none(self):
        entries = [_entry(lot="采购包1"), _entry(lot="采购包2")]
        assert build_volume_closure_payload(entries) is None

    def test_single_scheme_basis_is_volume_max(self):
        payload = build_volume_closure_payload(_doc3479_entries())
        assert payload["basis"] == "volume_max"

    def test_parallel_hundred_unit_weights_verified(self):
        """并行百分制: 各卷盖章 weight=1.0 是卷内满分, 不是文档折算轴。"""
        entries = [
            _entry(score_weight=40, volume="commercial", volume_label="商务评分",
                   volume_weight=1.0, volume_max=100, diagnostic_name="财务状况"),
            _entry(score_weight=60, volume="commercial", volume_label="商务评分",
                   volume_weight=1.0, volume_max=100, diagnostic_name="施工单位业绩"),
            _entry(score_weight=55, volume="technical", volume_label="技术评分",
                   volume_weight=1.0, volume_max=100, diagnostic_name="工程概述"),
            _entry(score_weight=45, volume="technical", volume_label="技术评分",
                   volume_weight=1.0, volume_max=100, diagnostic_name="进度计划"),
            _entry(score_weight=100, volume="price", volume_label="价格评分",
                   volume_weight=1.0, volume_max=100, diagnostic_name="投标报价"),
        ]
        payload = build_volume_closure_payload(entries)
        assert payload["verified"] is True
        assert payload["reason"] is None

    def test_credit_service_unit_weights_verified(self):
        """同形态第二例: 资信/服务各 100 且 weight=1.0, 不得走 weight_sum。"""
        entries = [
            _entry(score_weight=70, volume="qualification", volume_label="资信评分",
                   volume_weight=1.0, volume_max=100, diagnostic_name="企业资质"),
            _entry(score_weight=30, volume="qualification", volume_label="资信评分",
                   volume_weight=1.0, volume_max=100, diagnostic_name="信誉记录"),
            _entry(score_weight=100, volume="service", volume_label="服务评分",
                   volume_weight=1.0, volume_max=100, diagnostic_name="售后响应"),
        ]
        payload = build_volume_closure_payload(entries)
        assert payload["verified"] is True

    def test_fold_weights_still_require_sum_one(self):
        """折算制成果: 真实折算份额 0.3 整卷漏提仍拦截。"""
        entries = [_entry(score_weight=100, volume="price", volume_label="报价部分",
                          volume_weight=0.3, diagnostic_name="响应报价")]
        payload = build_volume_closure_payload(entries)
        assert payload["verified"] is False
        assert payload["reason"] in ("weight_sum", "weight_partial")

    def test_mixed_unit_and_fold_weight_not_skipped(self):
        """一卷 1.0、一卷 0.4 不是并行百分制, 仍走折算轴探测。"""
        entries = [
            _entry(score_weight=100, volume="technical", volume_label="技术评分",
                   volume_weight=1.0, volume_max=100, diagnostic_name="技术方案"),
            _entry(score_weight=100, volume="commercial", volume_label="商务评分",
                   volume_weight=0.4, volume_max=100, diagnostic_name="商务资信"),
        ]
        payload = build_volume_closure_payload(entries)
        assert payload["verified"] is False
        assert payload["reason"] == "weight_sum"

    def test_unit_weight_plus_missing_weight_still_partial(self):
        """一卷盖章 1.0、另一卷未声明, 不得当成并行百分制跳过。"""
        entries = [
            _entry(score_weight=100, volume="technical", volume_label="技术评分",
                   volume_weight=1.0, volume_max=100, diagnostic_name="技术方案"),
            _entry(score_weight=100, volume="commercial", volume_label="商务评分",
                   volume_weight=None, volume_max=100, diagnostic_name="商务资信"),
        ]
        payload = build_volume_closure_payload(entries)
        assert payload["verified"] is False
        assert payload["reason"] == "weight_partial"


def _scheme_entry(scheme: str, name: str, weight: float, vmax: float = 100.0):
    return _entry(
        scheme=scheme, score_weight=weight, volume_max=vmax,
        volume_label=f"{scheme}技术部分", diagnostic_name=name,
    )


class TestPerSchemeClosure:
    """3484 形态: 多详评模板文档按 scheme 分区独立闭合。"""

    def test_two_schemes_each_closed_verified(self):
        entries = [
            _scheme_entry("JS-FWTY", "方案甲", 60), _scheme_entry("JS-FWTY", "业绩甲", 40),
            _scheme_entry("JS-GCSJ02", "方案乙", 70), _scheme_entry("JS-GCSJ02", "业绩乙", 30),
        ]
        payload = build_volume_closure_payload(entries)
        assert payload["verified"] is True
        assert payload["basis"] == "per_scheme"
        assert payload["schemes"] == ["JS-FWTY", "JS-GCSJ02"]

    def test_one_scheme_gap_not_verified(self):
        """3484 实测形态: JS-GCSJ02 只提到部分项 → 该方案不闭合 → 整体不判绿。"""
        entries = [
            _scheme_entry("JS-FWTY", "方案甲", 60), _scheme_entry("JS-FWTY", "业绩甲", 40),
            _scheme_entry("JS-GCSJ02", "方案乙", 20),
        ]
        payload = build_volume_closure_payload(entries)
        assert payload["verified"] is False
        assert "JS-GCSJ02" in payload["reason"]

    def test_mixed_unassigned_scheme_fails_closed(self):
        entries = [
            _scheme_entry("JS-FWTY", "方案甲", 100),
            _entry(scheme=None, score_weight=100, diagnostic_name="游离项"),
        ]
        payload = build_volume_closure_payload(entries)
        assert payload["verified"] is False
        assert payload["reason"] == "scheme_partial"

    def test_baseline_conflict_not_verified(self):
        entries = [
            _entry(score_weight=50, volume_max=100, diagnostic_name="甲"),
            _entry(score_weight=50, volume_max=80, diagnostic_name="乙"),
        ]
        assert build_volume_closure_payload(entries)["reason"] == "baseline_conflict"


# ---------------------------------------------------------------------------
# verdict 闭合豁免（判绿单源: chunk账本噪音让位于PDF闭合证据）
# ---------------------------------------------------------------------------

def _noisy_ledger(**overrides) -> LedgerReport:
    """chunk 声明捕获失准的典型噪音账本(3479 形态: L4 低置信+未归组)。"""
    base = dict(
        status=LedgerStatus.REVIEW_REQUIRED,
        basis_level=BasisLevel.L4_NONE,
        basis_low_confidence=True,
        expected_total=None,
        extracted_total=160.0,
        total_gap=None,
        unlabeled_sum=84.0,
    )
    base.update(overrides)
    return LedgerReport(**base)


def _payload(**overrides) -> dict:
    payload = {
        "needs_review": False,
        "note": "",
        "by_volume": {},
        "by_lot": {},
        "volume_checks": {},
        "weight_sum_ok": True,
        "item_count": 12,
        "missing_score_count": 0,
        "missing_rule_count": 0,
        "structure_review_required": False,
        "diagnostics": [],
        "volume_closure": {"verified": True, "reason": None},
    }
    payload.update(overrides)
    return payload


class TestClosureStrongVerdict:
    def test_closure_verified_exempts_ledger_noise(self):
        assert resolve_scoring_verdict(_payload(), _noisy_ledger()) == VERDICT_GREEN

    def test_closure_verified_exempts_legacy_needs_review(self):
        payload = _payload(needs_review=True)
        assert resolve_scoring_verdict(payload, _noisy_ledger()) == VERDICT_GREEN

    def test_missing_rules_do_not_block_green(self):
        """用户裁决: 缺评审标准仅提示, 不阻断闭合判绿。"""
        payload = _payload(needs_review=True, missing_rule_count=3)
        assert resolve_scoring_verdict(payload, _noisy_ledger()) == VERDICT_GREEN

    def test_closure_not_verified_keeps_amber(self):
        payload = _payload(
            needs_review=True,
            volume_closure={"verified": False, "reason": "group_gap"},
        )
        assert resolve_scoring_verdict(payload, _noisy_ledger()) == VERDICT_AMBER

    def test_structure_review_required_blocks_closure_green(self):
        payload = _payload(needs_review=True, structure_review_required=True)
        assert resolve_scoring_verdict(payload, _noisy_ledger()) == VERDICT_AMBER

    def test_closure_absent_keeps_existing_formula(self):
        payload = _payload(needs_review=True)
        payload.pop("volume_closure")
        assert resolve_scoring_verdict(payload, _noisy_ledger()) == VERDICT_AMBER

    def test_multi_scheme_signal_blocks_closure_green(self):
        """宪法附则(同款): 整套方案漏提时单方案闭合是假象, 多方案信号否决。"""
        ledger = _noisy_ledger(multi_scheme_signal="检测到多套评分方案")
        assert resolve_scoring_verdict(_payload(), ledger) == VERDICT_AMBER

    def test_parallel_unit_weights_closure_greens_l4_ledger(self):
        """并行百分制闭合强证据可豁免 L4 无总分账本噪音。"""
        closure = build_volume_closure_payload([
            _entry(score_weight=100, volume="commercial", volume_label="商务评分",
                   volume_weight=1.0, volume_max=100, diagnostic_name="商务项"),
            _entry(score_weight=100, volume="technical", volume_label="技术评分",
                   volume_weight=1.0, volume_max=100, diagnostic_name="技术项"),
            _entry(score_weight=100, volume="price", volume_label="价格评分",
                   volume_weight=1.0, volume_max=100, diagnostic_name="价格项"),
        ])
        assert closure is not None and closure["verified"] is True
        payload = _payload(volume_closure=closure)
        assert resolve_scoring_verdict(payload, _noisy_ledger()) == VERDICT_GREEN

    def test_per_scheme_closure_overrides_multi_scheme_signal(self):
        """3484: 条目带方案轴且逐方案闭合(basis=per_scheme)时, 方案轴已被承接,
        多方案信号不再阻断绿。"""
        payload = _payload(
            volume_closure={"verified": True, "reason": None, "basis": "per_scheme"},
        )
        ledger = _noisy_ledger(multi_scheme_signal="检测到多套评分方案")
        assert resolve_scoring_verdict(payload, ledger) == VERDICT_GREEN

    def test_scheme_field_flows_from_draft_to_item(self):
        """契约承接: 模型输出的 scheme 字段透传到 ExtractedScoringItem。"""
        from app.core.extractor.scoring.vlm.candidate_contract import (
            parse_item_drafts,
            to_extracted_items,
        )

        drafts, invalid = parse_item_drafts([
            {"score_item_name": "业绩情况", "score_weight": 20,
             "score_rules": ["每提供一项得5分"], "scheme": "JS-FWTY：服务技术详评模板"},
            {"score_item_name": "服务方案", "score_weight": 30,
             "score_rules": ["综合评审"], "scheme": "  "},
        ])
        assert invalid == 0
        items = to_extracted_items(drafts, {})
        assert items[0].scheme == "JS-FWTY：服务技术详评模板"
        assert items[1].scheme is None

    def test_prompts_instruct_scheme_field(self):
        from app.core.ai.prompts.scoring_pdf_rescue import SCORING_PDF_RESCUE_USER
        from app.core.ai.prompts.scoring_vlm_rescue import SCORING_VLM_RESCUE_USER

        assert '"scheme" 字段' in SCORING_PDF_RESCUE_USER
        assert '"scheme" 字段' in SCORING_VLM_RESCUE_USER


class TestPromptConstitutionAlignment:
    """宪法对齐契约: 命名契约与证据轴一致、守恒自检存在、两通道同文。"""

    def test_pdf_prompt_allows_self_named_child_with_verbatim_rules(self):
        from app.core.ai.prompts.scoring_pdf_rescue import SCORING_PDF_RESCUE_USER

        assert "自拟" in SCORING_PDF_RESCUE_USER
        assert "score_rules 必须逐字照抄" in SCORING_PDF_RESCUE_USER
        assert "自检守恒" in SCORING_PDF_RESCUE_USER

    def test_vlm_prompt_mirrors_naming_contract(self):
        from app.core.ai.prompts.scoring_vlm_rescue import SCORING_VLM_RESCUE_USER

        assert "自拟" in SCORING_VLM_RESCUE_USER
        assert "自检守恒" in SCORING_VLM_RESCUE_USER

    def test_prompts_keep_production_form_rules(self):
        """有机结合: 生产沉淀的形态规则(折算/档次行/价格公式/排除项)不得丢失。"""
        from app.core.ai.prompts.scoring_pdf_rescue import SCORING_PDF_RESCUE_SYSTEM

        for term in ("禁止自行折算", "档次行", "price_formula", "排除项"):
            assert term in SCORING_PDF_RESCUE_SYSTEM


# ---------------------------------------------------------------------------
# JSONL 宽容解析回退（补全: 16k字回复不再判零项）
# ---------------------------------------------------------------------------

class TestLenientJsonlFallback:
    def test_pretty_printed_objects_recovered(self):
        text = (
            '说明文字\n{\n  "type": "header",\n  "declared_total": 100\n}\n'
            '{\n  "score_item_name": "响应报价",\n  "score_weight": 100\n}\n'
            '{\n  "score_item_name": "承包商业绩",\n  "score_weight": 60\n}'
        )
        header, items, _broken = extract_jsonl_records(text)
        assert header is not None and header["declared_total"] == 100
        assert [i["score_item_name"] for i in items] == ["响应报价", "承包商业绩"]

    def test_array_wrapped_multiline_objects_recovered(self):
        text = (
            '[\n  {\n    "score_item_name": "甲",\n    "score_weight": 10\n  },\n'
            '  {\n    "score_item_name": "乙",\n    "score_weight": 20\n  }\n]'
        )
        _header, items, _broken = extract_jsonl_records(text)
        assert [i["score_item_name"] for i in items] == ["甲", "乙"]

    def test_braces_inside_strings_do_not_split_blocks(self):
        text = '{\n "score_item_name": "含{花括号}项",\n "score_weight": 5\n}'
        _header, items, _broken = extract_jsonl_records(text)
        assert len(items) == 1 and items[0]["score_item_name"] == "含{花括号}项"

    def test_line_mode_unchanged_when_items_found(self):
        text = (
            '{"type": "header", "declared_total": 100}\n'
            '{"score_item_name": "甲", "score_weight": 10}\n'
            "{broken-line}"
        )
        header, items, broken = extract_jsonl_records(text)
        assert header is not None and len(items) == 1 and broken == 1


# ---------------------------------------------------------------------------
# 合成子行名锚定放行（父段+规则原文双证据）
# ---------------------------------------------------------------------------

_RULE_6 = (
    "项目团队人员中（项目负责人除外）持有与本项目相关专业副高级及以上"
    "职称证书的，有2人，得4分；在此基础上每增加1人，得2分，满分6分。"
)


def _anchor(*texts: str) -> str:
    from app.core.extractor.scoring.vlm.candidate_gates import _compact

    return _compact("".join(texts))


def _gate_item(name: str, weight: float, rules: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        score_item_name=name, score_weight=weight,
        score_rules=rules, deduction_rules=[],
    )


class TestCompositeChildAnchoring:
    def test_paraphrased_child_with_verbatim_rule_kept(self):
        """3479 实测形态: 子行名概括改写、规则逐字、分值在原文 → 放行。"""
        from app.core.extractor.scoring.vlm.candidate_gates import (
            filter_anchored_items,
        )

        anchor = _anchor("序号 项目管理机构设置 ", _RULE_6, " 分值 6")
        item = _gate_item(
            "项目管理机构设置-副高级及以上职称人员数量", 6.0, [_RULE_6]
        )
        kept, dropped = filter_anchored_items([item], anchor)
        assert len(kept) == 1 and not dropped

    def test_paraphrased_child_with_paraphrased_rule_dropped(self):
        """名称与规则均改写(幻觉行形态) → 照拒。"""
        from app.core.extractor.scoring.vlm.candidate_gates import (
            filter_anchored_items,
        )

        anchor = _anchor("序号 项目管理机构设置 ", _RULE_6, " 分值 6")
        item = _gate_item(
            "项目管理机构设置-团队职称配置", 6.0, ["按职称人数分档给分"]
        )
        kept, dropped = filter_anchored_items([item], anchor)
        assert not kept and len(dropped) == 1

    def test_paraphrased_child_with_unanchored_score_dropped(self):
        """分值红线不动摇: 规则逐字但分值未见于原文 → 拒收。"""
        from app.core.extractor.scoring.vlm.candidate_gates import (
            filter_anchored_items,
        )

        anchor = _anchor("项目管理机构设置 ", _RULE_6.replace("满分6分", "满分若干"))
        item = _gate_item(
            "项目管理机构设置-副高级及以上职称人员数量", 99.0,
            [_RULE_6.replace("满分6分", "满分若干")],
        )
        kept, dropped = filter_anchored_items([item], anchor)
        assert not kept and len(dropped) == 1

    def test_non_composite_paraphrase_still_dropped(self):
        """非复合名的整名改写不受本放行影响。"""
        from app.core.extractor.scoring.vlm.candidate_gates import (
            filter_anchored_items,
        )

        anchor = _anchor("服务方案编制 对本项目的服务方案编制的全面性 13")
        item = _gate_item("方案编写水平", 13.0, ["自由发挥"])
        kept, dropped = filter_anchored_items([item], anchor)
        assert not kept and len(dropped) == 1

    def test_retry_feedback_carries_dropped_reasons(self):
        """: 拒收原因进重询反馈, 模型下轮可自我纠正命名。"""
        from app.core.extractor.scoring.vlm.rescue_rounds import (
            _build_retry_feedback,
        )

        feedback = _build_retry_feedback(
            SimpleNamespace(feedback=""), kept=[],
            dropped=["项目管理机构设置-职称配置(名称未见于原文)"],
            scoring_chunks=[],
        )
        assert "职称配置" in feedback
        assert "逐字" in feedback


# ---------------------------------------------------------------------------
# 模式分叉与扫描件兜底次序
# ---------------------------------------------------------------------------

def _extractor(monkeypatch, chunks):
    from app.core.extractor.scoring.scoring_extractor import ScoringExtractor

    extractor = ScoringExtractor.__new__(ScoringExtractor)
    extractor.db = None
    extractor.llm_client = SimpleNamespace()
    extractor.project_id = None
    extractor.max_retries = 0
    extractor.progress_callback = None

    async def _fake_chunks(_document_id):
        return chunks

    monkeypatch.setattr(extractor, "_get_scoring_chunks", _fake_chunks)
    return extractor


class TestModeDispatch:
    async def test_pdf_direct_returns_direct_outcome(self, monkeypatch):
        sentinel = object()

        async def _fake_direct(**_kwargs):
            return sentinel

        monkeypatch.setattr(
            "app.core.extractor.scoring.pdf_chat.direct_primary."
            "collect_structure_via_pdf_direct",
            _fake_direct,
        )
        extractor = _extractor(monkeypatch, chunks=[SimpleNamespace(id=1)])
        assert await extractor.collect_scoring_structure(2517) is sentinel

    async def test_pdf_direct_none_falls_to_zero_chunk_vlm(self, monkeypatch):
        """扫描件契约: 直读输入不可用且零chunk → 落VLM零chunk入口。"""

        async def _fake_direct(**_kwargs):
            return None

        sentinel = object()

        async def _fake_zero_chunk(**_kwargs):
            return sentinel

        monkeypatch.setattr(
            "app.core.extractor.scoring.pdf_chat.direct_primary."
            "collect_structure_via_pdf_direct",
            _fake_direct,
        )
        monkeypatch.setattr(
            "app.core.extractor.scoring.vlm.zero_chunk_entry."
            "collect_structure_via_vlm_when_no_chunks",
            _fake_zero_chunk,
        )
        extractor = _extractor(monkeypatch, chunks=[])
        assert await extractor.collect_scoring_structure(2517) is sentinel

    async def test_absent_presence_skips_subpdf_and_vlm(self, monkeypatch):
        """: 无表门禁阻断时不组装子PDF、不落 VLM。"""
        from app.core.extractor.scoring.pdf_chat import direct_primary
        from app.core.extractor.scoring.scoring_table_presence import PresenceDecision

        called = {"pdf": 0, "vlm": 0}

        async def _should_not_direct(**_kwargs):
            called["pdf"] += 1
            return None

        async def _should_not_vlm(**_kwargs):
            called["vlm"] += 1
            return object()

        async def _absent_gate(_db, _document_id, _chunks):
            return PresenceDecision("absent", "competitive", ("no_scoring_structure",), ())

        monkeypatch.setattr(
            "app.core.extractor.scoring.scoring_table_presence."
            "evaluate_document_scoring_presence",
            _absent_gate,
        )
        monkeypatch.setattr(
            "app.core.extractor.scoring.pdf_chat.direct_primary."
            "collect_structure_via_pdf_direct",
            _should_not_direct,
        )
        monkeypatch.setattr(
            "app.core.extractor.scoring.vlm.zero_chunk_entry."
            "collect_structure_via_vlm_when_no_chunks",
            _should_not_vlm,
        )
        import time

        extractor = _extractor(monkeypatch, chunks=[SimpleNamespace(id=1, page_num=2)])
        outcome = await direct_primary.try_pdf_direct_primary(
            extractor, 1039, [SimpleNamespace(id=1, page_num=2)], time.perf_counter()
        )
        assert called["pdf"] == 0
        assert called["vlm"] == 0
        assert outcome.items == []
        assert outcome.verdict_probe is not None
        assert outcome.verdict_probe["presence"] == "absent"
        assert any("未检出投标评分表" in d for d in outcome.parse_diagnostics)

    def test_scoring_extractor_file_under_hard_threshold(self):
        """复杂度护栏(阶段3接管自退役的 rescue_integration 契约测试)。"""
        from pathlib import Path

        import app.core.extractor.scoring.scoring_extractor as mod

        lines = len(Path(mod.__file__).read_text(encoding="utf-8").splitlines())
        assert lines <= 800, f"scoring_extractor.py 当前 {lines} 行"


# ---------------------------------------------------------------------------
# 子PDF装配(自退役的 test_scoring_pdf_rescue.TestInputBuilder 移植)
# ---------------------------------------------------------------------------


class TestInputBuilder:
    async def test_subpdf_assembly_pages_and_anchor(self, tmp_path):
        import base64

        import fitz

        from app.core.extractor.scoring.pdf_chat.pdf_rescue_input_builder import (
            build_pdf_chat_rescue_input,
        )

        pdf_path = tmp_path / "tender.pdf"
        doc = fitz.open()
        texts = [
            "第一章 招标公告 预算金额90万元",
            "评标办法 评分标准 评分因素 分值 技术方案（40分） 价格评审（60分）",
            "第三章 合同条款",
            "第四章 附件",
        ]
        for text in texts:
            page = doc.new_page()
            # 内置简体中文字体, 默认helv无中文字形会导致文本层乱码
            page.insert_text((72, 72), text, fontname="china-s")
        doc.save(str(pdf_path))
        doc.close()

        class _FakeDB:
            async def execute(self, stmt):
                return SimpleNamespace(
                    scalar_one_or_none=lambda: SimpleNamespace(
                        file_path=str(pdf_path)
                    )
                )

        rescue_input = await build_pdf_chat_rescue_input(
            db=_FakeDB(), document_id=7,
            scoring_chunks=[SimpleNamespace(page_num=2)],
            page_margin=1, max_pages=30,
        )
        assert rescue_input is not None
        assert rescue_input.pages == (1, 2, 3)
        assert rescue_input.filename == "doc7_scoring_p1-3.pdf"
        sub = fitz.open(stream=rescue_input.pdf_bytes, filetype="pdf")
        assert sub.page_count == 3
        sub.close()
        assert base64.b64decode(rescue_input.pdf_base64) == rescue_input.pdf_bytes
        # 锚定文本层来自截取页(NFKC归一: 全角括号转半角)
        assert "技术方案(40分)" in rescue_input.anchor_text
        assert "价格评审(60分)" in rescue_input.anchor_text

    async def test_missing_pdf_returns_none(self):
        from app.core.extractor.scoring.pdf_chat.pdf_rescue_input_builder import (
            build_pdf_chat_rescue_input,
        )

        class _FakeDB:
            async def execute(self, stmt):
                return SimpleNamespace(
                    scalar_one_or_none=lambda: SimpleNamespace(
                        file_path="Z:/not/exists.pdf"
                    )
                )

        rescue_input = await build_pdf_chat_rescue_input(
            db=_FakeDB(), document_id=8,
            scoring_chunks=[], page_margin=1, max_pages=30,
        )
        assert rescue_input is None


# ---------------------------------------------------------------------------
# direct_primary 输入不可用: fail-visible 空产物
# ---------------------------------------------------------------------------

class TestDirectPrimaryUnavailableInput:
    async def test_input_none_with_chunks_returns_empty_amber(self, monkeypatch):
        from app.core.extractor.scoring.pdf_chat import direct_primary

        async def _no_input(**_kwargs):
            return None

        monkeypatch.setattr(
            "app.core.extractor.scoring.pdf_chat.pdf_rescue_input_builder."
            "build_pdf_chat_rescue_input",
            _no_input,
        )
        import time

        outcome = await direct_primary.collect_structure_via_pdf_direct(
            db=None, document_id=1, project_id=None,
            scoring_chunks=[SimpleNamespace(id=1, page_num=32)],
            progress_callback=None, started=time.perf_counter(),
        )
        assert outcome is not None
        assert outcome.items == []
        assert outcome.report.needs_review is True
        assert any("未能直读提取" in d for d in outcome.parse_diagnostics)
        assert outcome.verdict_probe is not None

    async def test_truncated_window_surfaces_user_visible_diagnostic(self, monkeypatch):
        """3484: 页窗口截断必须落用户可见诊断, 禁止静默不完整。"""
        from app.core.extractor.scoring.pdf_chat import direct_primary

        async def _truncated_input(**_kwargs):
            return SimpleNamespace(
                truncated=True, pages=(21, 50), pdf_bytes=b"x",
                pdf_base64="eA==", anchor_text="x", filename="f.pdf",
            )

        monkeypatch.setattr(
            "app.core.extractor.scoring.pdf_chat.pdf_rescue_input_builder."
            "build_pdf_chat_rescue_input",
            _truncated_input,
        )
        monkeypatch.setattr(
            "app.core.ai.pdf_transport.resolve_pdf_transport_targets", lambda: []
        )
        import time

        chunks = [SimpleNamespace(id=i, page_num=p) for i, p in enumerate((8, 33, 72))]
        outcome = await direct_primary.collect_structure_via_pdf_direct(
            db=None, document_id=1, project_id=None, scoring_chunks=chunks,
            progress_callback=None, started=time.perf_counter(),
        )
        joined = "".join(outcome.parse_diagnostics)
        assert "第8-72页" in joined
        assert "仅直读第21-50页" in joined
        assert "可能不完整" in joined

    async def test_input_none_without_chunks_returns_none(self, monkeypatch):
        from app.core.extractor.scoring.pdf_chat import direct_primary

        async def _no_input(**_kwargs):
            return None

        monkeypatch.setattr(
            "app.core.extractor.scoring.pdf_chat.pdf_rescue_input_builder."
            "build_pdf_chat_rescue_input",
            _no_input,
        )
        import time

        outcome = await direct_primary.collect_structure_via_pdf_direct(
            db=None, document_id=1, project_id=None, scoring_chunks=[],
            progress_callback=None, started=time.perf_counter(),
        )
        assert outcome is None
