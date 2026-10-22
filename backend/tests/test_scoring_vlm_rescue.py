"""评分 VLM 视觉救援通道防御测试。

覆盖: 触发条件(绿色/off/非绿)、采纳原子性(门禁拒绝时现状零改动)、
      虚构分值负例(泛化集04实测形态)、fail-open(无输入)、Repair反馈重询、
      契约解析(JSONL截断/自引用/对称因素/构成表副本)、映射语义。
不做: 不调真实LLM、不访问DB、不读文件系统(输入装配用桩替身)。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.extractor.scoring.scoring_rescue_integration import (
    ScoringRescueIntegrationResult,
)
from app.core.extractor.scoring.vlm import attempt_vlm_scoring_rescue
from app.core.extractor.scoring.vlm.candidate_contract import (
    VlmScoringItemDraft,
    extract_jsonl_records,
    normalize_vlm_items,
    to_extracted_items,
)
from app.core.extractor.scoring.vlm.candidate_gates import (
    check_evidence_anchoring,
    evaluate_candidate_gates,
    filter_anchored_items,
)
from app.core.extractor.scoring.vlm.rescue_input_builder import (
    VlmRescueInput,
    collect_rescue_pages,
)
from app.core.extractor.scoring.vlm.scoring_page_locator import locate_scoring_pages

pytestmark = [pytest.mark.anyio, pytest.mark.scoring]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _enforce_vlm_rescue_mode(monkeypatch):
    """测试默认 enforce, 避免进程环境变量 SCORING_VLM_RESCUE_MODE=off 污染。"""
    from app.core import scoring_vlm_rescue_config as cfg_mod

    monkeypatch.setattr(
        cfg_mod.scoring_vlm_rescue_settings, "SCORING_VLM_RESCUE_MODE", "enforce"
    )


def _make_tail(needs_review: bool = True) -> ScoringRescueIntegrationResult:
    return ScoringRescueIntegrationResult(
        items=[SimpleNamespace(score_item_name="旧项", score_weight=50.0)],
        report=SimpleNamespace(
            needs_review=needs_review, total=50.0, expected_total=100.0, note="待复核"
        ),
        fidelity_sidecar={},
        diagnostics=["existing-diag"],
        l3_diagnostics=[],
        adopted=False,
        mode="shadow",
    )


def _stub_input(anchor_text: str) -> VlmRescueInput:
    return VlmRescueInput(
        images=("aW1n",), pages=(1,), anchor_text=anchor_text, truncated=False
    )


class _FakeVisionClient:
    """按调用轮次返回预置响应的 vision 客户端替身(记录每轮 user prompt)。"""

    def __init__(self, responses: list[str]):
        self.responses = responses
        self.calls = 0
        self.prompts: list[str] = []

    async def chat_with_images(self, *args, **kwargs) -> str:
        self.prompts.append(str(args[0]) if args else "")
        text = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return text


_GOOD_JSONL = (
    '{"type": "header", "declared_total": 100, "volume_totals": []}\n'
    '{"type": "item", "score_item_name": "技术方案", "score_weight": 40,'
    ' "group_path": [], "fact_type": "base_score", "score_rules": ["优良中差"], "source_page": 1}\n'
    '{"type": "item", "score_item_name": "价格评审", "score_weight": 60,'
    ' "group_path": [], "fact_type": "price_formula", "score_rules": ["基准价法"], "source_page": 1}\n'
)
_GOOD_ANCHOR = "技术方案40分价格评审60分总分100分"

# 泛化集04实测形态: 符合性审查表条目被编造分值, 原文无该分值
_FABRICATED_JSONL = (
    '{"type": "item", "score_item_name": "投标文件格式", "score_weight": 10,'
    ' "group_path": [], "fact_type": "base_score", "score_rules": [], "source_page": 1}\n'
)

# doc2511实测形态: 名称/分值真实但分组标签伪造(照抄提示词示例的"技术卷")
_FABRICATED_LABEL_JSONL = (
    '{"type": "header", "declared_total": 100, "volume_totals": []}\n'
    '{"type": "item", "score_item_name": "技术方案", "score_weight": 40,'
    ' "group_path": ["示例卷名"], "fact_type": "base_score", "score_rules": ["优良中差"], "source_page": 1}\n'
    '{"type": "item", "score_item_name": "价格评审", "score_weight": 60,'
    ' "group_path": ["技术卷"], "fact_type": "price_formula", "score_rules": ["基准价法"], "source_page": 1}\n'
)


def _channel_env(monkeypatch, *, tail, rescue_input, client):
    """统一打桩: 输入装配/vision客户端/Registry运行参数/verdict终点复判。

 默认复判桩与既有夹具语义对齐: 候选总分≈100 判 green(等价旧'闭合即终点'),
 专项测试可覆写为按分组差额判 amber。
 """
    from app.core.ai import ai_factory, prompt_registry
    from app.core.extractor.scoring.vlm import rescue_input_builder, rescue_rounds

    async def _fake_build(**kwargs):
        return rescue_input

    async def _fake_judge(**kwargs):
        total = sum(float(getattr(i, "score_weight", 0) or 0) for i in kwargs["items"])
        return ("green" if abs(total - 100.0) <= 1.0 else "amber", None)

    monkeypatch.setattr(rescue_input_builder, "build_vlm_rescue_input", _fake_build)
    monkeypatch.setattr(ai_factory, "get_vision_client", lambda project_id=None: client)
    monkeypatch.setattr(rescue_rounds, "_judge_candidate_verdict", _fake_judge)
    monkeypatch.setattr(
        prompt_registry, "resolve_runtime_params", lambda node, t, m: (t, m)
    )
    monkeypatch.setattr(prompt_registry, "resolve_system_prompt", lambda node, d: d)
    monkeypatch.setattr(prompt_registry, "resolve_user_template", lambda node, d: d)


async def _invoke(tail):
    return await attempt_vlm_scoring_rescue(
        tail, db=object(), document_id=1, project_id=None,
        scoring_chunks=[SimpleNamespace(content=_GOOD_ANCHOR, page_num=1)],
        volume_defs={},
    )


class TestTriggerConditions:
    async def test_green_report_returns_same_object_without_side_effects(
        self, monkeypatch
    ):
        tail = _make_tail(needs_review=False)
        from app.core.extractor.scoring.vlm import rescue_input_builder

        def _boom(**kwargs):  # 绿色路径绝不应装配输入(零渲染成本承诺)
            raise AssertionError("green path must not build input")

        monkeypatch.setattr(rescue_input_builder, "build_vlm_rescue_input", _boom)
        result = await _invoke(tail)
        assert result is tail
        assert tail.l3_diagnostics == []

    async def test_off_mode_returns_same_object(self, monkeypatch):
        from app.core import scoring_vlm_rescue_config as cfg_mod

        monkeypatch.setattr(
            cfg_mod.scoring_vlm_rescue_settings, "SCORING_VLM_RESCUE_MODE", "off"
        )
        tail = _make_tail(needs_review=True)
        result = await _invoke(tail)
        assert result is tail
        assert tail.l3_diagnostics == []

    async def test_no_input_fail_open_keeps_tail_with_diagnostic(self, monkeypatch):
        tail = _make_tail(needs_review=True)
        _channel_env(
            monkeypatch, tail=tail, rescue_input=None, client=_FakeVisionClient([""])
        )
        result = await _invoke(tail)
        assert result is tail
        assert tail.l3_diagnostics[-1]["status"] == "skipped_no_input"

    async def test_trigger_non_green_overrides_legacy_false_green(self, monkeypatch):
        """ doc2478 形态: legacy 假绿(needs_review=False)但权威判定非绿,
 救援必须启动并可采纳(触发源不再被 legacy 报告一票否决)。"""
        tail = _make_tail(needs_review=False)
        client = _FakeVisionClient([_GOOD_JSONL])
        _channel_env(
            monkeypatch, tail=tail,
            rescue_input=_stub_input(_GOOD_ANCHOR), client=client,
        )
        result = await attempt_vlm_scoring_rescue(
            tail, db=object(), document_id=1, project_id=None,
            scoring_chunks=[SimpleNamespace(content=_GOOD_ANCHOR, page_num=1)],
            volume_defs={}, trigger_non_green=True,
        )
        assert result is not tail
        assert result.adopted is True
        assert result.report.needs_review is False

    async def test_trigger_green_verdict_skips_even_when_legacy_needs_review(
        self, monkeypatch
    ):
        """权威判定为绿时(如 legacy 被账本让位)不再空跑渲染与 vision 调用。"""
        tail = _make_tail(needs_review=True)
        from app.core.extractor.scoring.vlm import rescue_input_builder

        def _boom(**kwargs):
            raise AssertionError("green verdict must not build input")

        monkeypatch.setattr(rescue_input_builder, "build_vlm_rescue_input", _boom)
        result = await attempt_vlm_scoring_rescue(
            tail, db=object(), document_id=1, project_id=None,
            scoring_chunks=[SimpleNamespace(content=_GOOD_ANCHOR, page_num=1)],
            volume_defs={}, trigger_non_green=False,
        )
        assert result is tail
        assert tail.l3_diagnostics == []


class TestAtomicity:
    async def test_gate_rejection_keeps_original_items_and_report(self, monkeypatch):
        tail = _make_tail(needs_review=True)
        original_items = list(tail.items)
        client = _FakeVisionClient([_FABRICATED_JSONL])
        _channel_env(
            monkeypatch, tail=tail,
            rescue_input=_stub_input(_GOOD_ANCHOR), client=client,
        )
        result = await _invoke(tail)
        assert result is tail
        assert result.items == original_items
        assert result.report.needs_review is True
        assert tail.l3_diagnostics[-1]["status"] == "rejected"

    async def test_client_exception_keeps_tail(self, monkeypatch):
        tail = _make_tail(needs_review=True)

        class _Broken:
            async def chat_with_images(self, *a, **k):
                raise RuntimeError("provider down")

        _channel_env(
            monkeypatch, tail=tail,
            rescue_input=_stub_input(_GOOD_ANCHOR), client=_Broken(),
        )
        result = await _invoke(tail)
        assert result is tail
        assert tail.l3_diagnostics[-1]["status"] == "error"


class TestAdoption:
    async def test_complete_candidate_adopted_and_flips_needs_review(
        self, monkeypatch
    ):
        tail = _make_tail(needs_review=True)
        client = _FakeVisionClient([_GOOD_JSONL])
        _channel_env(
            monkeypatch, tail=tail,
            rescue_input=_stub_input(_GOOD_ANCHOR), client=client,
        )
        result = await _invoke(tail)
        assert result is not tail
        assert result.adopted is True
        assert result.report.needs_review is False
        assert result.report.total == 100.0
        names = sorted(i.score_item_name for i in result.items)
        assert names == ["价格评审", "技术方案"]
        # 采纳标记经 fidelity sidecar 落库(零schema变更)
        assert any("vlm_rescue" in ann.note for ann in result.fidelity_sidecar.values())
        assert tail.l3_diagnostics[-1]["status"] == "ok"

    async def test_repair_round_recovers_after_first_rejection(self, monkeypatch):
        tail = _make_tail(needs_review=True)
        client = _FakeVisionClient([_FABRICATED_JSONL, _GOOD_JSONL])
        _channel_env(
            monkeypatch, tail=tail,
            rescue_input=_stub_input(_GOOD_ANCHOR), client=client,
        )
        result = await _invoke(tail)
        assert result.adopted is True
        assert client.calls == 2
        assert tail.l3_diagnostics[-1]["gate_rejections"] == [["unanchored"]]

    async def test_retry_rounds_until_complete_and_stop(self, monkeypatch):
        """阶段1重试: 未闭合轮携反馈继续, 撞到完整闭合即停(不再耗满轮数)。"""
        tail = _make_tail(needs_review=True)
        client = _FakeVisionClient([_PARTIAL_JSONL, _GOOD_JSONL, _WORSE_JSONL])
        _channel_env(
            monkeypatch, tail=tail,
            rescue_input=_stub_input(_GOOD_ANCHOR + _PARTIAL_ANCHOR), client=client,
        )
        result = await _invoke(tail)
        assert result.adopted is True
        assert result.report.needs_review is False
        assert result.report.total == 100.0
        assert client.calls == 2  # 第2轮完整闭合即停, 第3轮不再调用
        assert tail.l3_diagnostics[-1]["rounds"] == 2

    async def test_retry_keeps_best_round_across_variance(self, monkeypatch):
        """跨轮取优: 轮次质量波动时采纳缺口最小的一轮, 而非最后一轮。"""
        tail = _make_tail(needs_review=True)
        client = _FakeVisionClient([_PARTIAL_JSONL, _WORSE_JSONL, _WORSE_JSONL])
        _channel_env(
            monkeypatch, tail=tail,
            rescue_input=_stub_input(_PARTIAL_ANCHOR + "技术方案20分"), client=client,
        )
        result = await _invoke(tail)
        assert result.adopted is True
        assert result.report.needs_review is True
        assert result.report.total == 90.0  # 第1轮(缺口10)优于后两轮(缺口80)
        assert tail.l3_diagnostics[-1]["rounds"] == 3
        assert len(tail.l3_diagnostics[-1]["gate_rejections"]) == 3


def _misallocation_judge(monkeypatch):
    """ doc2484 复判桩: {39,31,30}构成判 green, 其余判 amber 并给分组差额账本。"""
    from app.core.extractor.scoring.vlm import rescue_rounds

    async def _judge(**kwargs):
        weights = sorted(float(getattr(i, "score_weight", 0) or 0) for i in kwargs["items"])
        if weights == [30.0, 31.0, 39.0]:
            return ("green", None)
        ledger = SimpleNamespace(
            group_rows=(
                SimpleNamespace(label="商务评审", declared=39.0, extracted_sum=34.0, gap=-5.0),
                SimpleNamespace(label="技术评审", declared=31.0, extracted_sum=36.0, gap=5.0),
            ),
            unlabeled_sum=0.0,
        )
        return ("amber", ledger)

    monkeypatch.setattr(rescue_rounds, "_judge_candidate_verdict", _judge)


# doc2484 形态: 总分守恒(100)但卷间错位——商务少5/技术多5, 闭合总分轴不可见
_MISALLOCATED_JSONL = (
    '{"type": "header", "declared_total": 100, "volume_totals": []}\n'
    '{"type": "item", "score_item_name": "商务评审", "score_weight": 34,'
    ' "group_path": [], "fact_type": "base_score", "score_rules": ["综合"], "source_page": 1}\n'
    '{"type": "item", "score_item_name": "技术评审", "score_weight": 36,'
    ' "group_path": [], "fact_type": "base_score", "score_rules": ["优良中差"], "source_page": 1}\n'
    '{"type": "item", "score_item_name": "价格评审", "score_weight": 30,'
    ' "group_path": [], "fact_type": "price_formula", "score_rules": ["基准价法"], "source_page": 1}\n'
)
_REALLOCATED_JSONL = (
    '{"type": "header", "declared_total": 100, "volume_totals": []}\n'
    '{"type": "item", "score_item_name": "商务评审", "score_weight": 39,'
    ' "group_path": [], "fact_type": "base_score", "score_rules": ["综合"], "source_page": 1}\n'
    '{"type": "item", "score_item_name": "技术评审", "score_weight": 31,'
    ' "group_path": [], "fact_type": "base_score", "score_rules": ["优良中差"], "source_page": 1}\n'
    '{"type": "item", "score_item_name": "价格评审", "score_weight": 30,'
    ' "group_path": [], "fact_type": "price_formula", "score_rules": ["基准价法"], "source_page": 1}\n'
)
_MISALLOC_ANCHOR = "商务评审34分39分技术评审36分31分价格评审30分总分100分"


class TestVerdictEndpoint:
    """: 重试终点必须消费 verdict 单源, 闭合 COMPLETE 无定局权。"""

    async def test_group_misallocation_retries_until_verdict_green(self, monkeypatch):
        """doc2484 形态: 首轮总分100但分组错位(闭合COMPLETE/verdict amber),
        必须携分组差额反馈重询而非定局; 次轮构成正确转绿即终点。"""
        tail = _make_tail(needs_review=True)
        client = _FakeVisionClient([_MISALLOCATED_JSONL, _REALLOCATED_JSONL])
        _channel_env(
            monkeypatch, tail=tail,
            rescue_input=_stub_input(_MISALLOC_ANCHOR), client=client,
        )
        _misallocation_judge(monkeypatch)
        result = await _invoke(tail)
        assert result.adopted is True
        assert client.calls == 2  # 旧缺陷: 第1轮闭合COMPLETE即break, 仅1次调用
        meta = tail.l3_diagnostics[-1]
        assert meta["rounds"] == 2
        assert meta["final_verdict"] == "green"
        assert meta["gate_rejections"] == [["verdict_amber"]]
        # 重询反馈必须携带逐组差额(可操作的纠偏信号, 而非泛泛not_closed)
        assert "分组核对未通过" in client.prompts[1]
        assert "商务评审: 要求39分" in client.prompts[1]

    async def test_three_rounds_non_green_adopts_best_with_final_verdict(
        self, monkeypatch
    ):
        """用户裁决: 三轮均未转绿才放弃重试, 采纳跨轮最优并如实记录终verdict,
        不得再以闭合报告的 needs_review=False 冒充转绿。"""
        tail = _make_tail(needs_review=True)
        client = _FakeVisionClient([_MISALLOCATED_JSONL] * 3)
        _channel_env(
            monkeypatch, tail=tail,
            rescue_input=_stub_input(_MISALLOC_ANCHOR), client=client,
        )
        _misallocation_judge(monkeypatch)
        result = await _invoke(tail)
        assert client.calls == 3  # 耗满轮数上限
        assert result.adopted is True  # 前端显示最优(闭合COMPLETE候选)
        meta = tail.l3_diagnostics[-1]
        assert meta["rounds"] == 3
        assert meta["final_verdict"] == "amber"  # 探针/日志不再误记为转绿

    def test_lot_backfill_and_hint_for_multi_lot_document(self):
        """ doc2481形态: 多标段 chunk 的 lot-页映射必须回填无 lot 候选,
 margin 扩页就近归段, 已带 lot 不覆盖; 提示片段含标段-页映射。"""
        from app.core.extractor.scoring.vlm.scope_context import (
            backfill_item_scopes_from_chunks,
            build_scope_page_hint,
        )
        from app.schemas.requirement import ExtractedScoringItem

        chunks = [
            SimpleNamespace(
                content="[评分表 第2页 · 标段三]", page_num=2,
                chunk_metadata={"scoring_payload": {
                    "lot": "标段三", "page_start": 2, "page_end": 2,
                }},
            ),
            SimpleNamespace(
                content="[评分表 第4-5页 · 标段四]", page_num=4,
                chunk_metadata={"scoring_payload": {
                    "lot": "标段四", "page_start": 4, "page_end": 5,
                }},
            ),
        ]
        hint = build_scope_page_hint(chunks)
        assert "标段三" in hint and "标段四" in hint
        assert "group_path" in hint

        def _item(name: str, page: int, lot: str | None = None) -> ExtractedScoringItem:
            return ExtractedScoringItem(
                category="TECH", score_item_name=name, description=f"{name}评分",
                score_weight=10.0, lot=lot, fact_type="base_score",
                score_rules=["规则"], deduction_rules=[], source_pages=[page],
            )

        items = [
            _item("跨页评分项", 1),      # margin 页(标段三表前半) -> 就近标段三
            _item("技术响应", 2),        # 标段三页
            _item("整体建设方案", 4),    # 标段四页
            _item("已带归属", 5, lot="标段四"),
        ]
        filled = backfill_item_scopes_from_chunks(items, chunks)
        assert [it.lot for it in filled] == ["标段三", "标段三", "标段四", "标段四"]

    def test_scheme_backfill_and_hint_for_multi_scheme_document(self):
        """审查V1(的多方案版): 多套评分方案 chunk 的 scheme-页映射必须
 回填无 scheme 候选并注入提示——防止两套方案合并单包轴恒拒。"""
        from app.core.extractor.scoring.vlm.scope_context import (
            backfill_item_scopes_from_chunks,
            build_scope_page_hint,
        )
        from app.schemas.requirement import ExtractedScoringItem

        chunks = [
            SimpleNamespace(
                content="[评分表 第2页]", page_num=2,
                chunk_metadata={"scoring_payload": {
                    "scheme": "scheme_1", "page_start": 2, "page_end": 2,
                }},
            ),
            SimpleNamespace(
                content="[评分表 第4页]", page_num=4,
                chunk_metadata={"scoring_payload": {
                    "scheme": "scheme_2", "page_start": 4, "page_end": 4,
                }},
            ),
        ]
        hint = build_scope_page_hint(chunks)
        assert "多套评分方案" in hint

        def _item(name: str, page: int) -> ExtractedScoringItem:
            return ExtractedScoringItem(
                category="TECH", score_item_name=name, description=f"{name}评分",
                score_weight=10.0, fact_type="base_score",
                score_rules=["规则"], deduction_rules=[], source_pages=[page],
            )

        filled = backfill_item_scopes_from_chunks(
            [_item("方案一项", 2), _item("方案二项", 4)], chunks
        )
        assert [it.scheme for it in filled] == ["scheme_1", "scheme_2"]

    def test_lot_backfill_noop_for_single_lot_document(self):
        """单标段文档零回填(避免给单包文档凭空造 lot)。"""
        from app.core.extractor.scoring.vlm.scope_context import (
            backfill_item_scopes_from_chunks,
            build_scope_page_hint,
        )

        chunks = [SimpleNamespace(
            content="[评分表 第2页]", page_num=2,
            chunk_metadata={"scoring_payload": {"page_start": 2, "page_end": 2}},
        )]
        assert build_scope_page_hint(chunks) == ""
        item = SimpleNamespace(lot=None, scheme=None, source_pages=[2])
        assert backfill_item_scopes_from_chunks([item], chunks) == [item]

    def test_authoritative_expected_gates_150_scale_document(self):
        """审查V2核心负例: 150分制文档传入权威基准后, "漏提凑满100"必须判
        not_closed(封堵静默转绿); 完整150则 COMPLETE。"""
        from app.core.extractor.scoring.scoring_scope_contracts import (
            ScoringValidationKind,
        )
        from app.core.extractor.scoring.vlm.candidate_gates import (
            check_business_closure,
        )
        from app.schemas.requirement import ExtractedScoringItem

        def _item(name: str, score: float) -> ExtractedScoringItem:
            return ExtractedScoringItem(
                category="TECH", score_item_name=name, description=f"{name}评分",
                score_weight=score, fact_type="base_score",
                score_rules=["规则"], deduction_rules=[], source_pages=[1],
            )

        under = [_item("技术", 60.0), _item("商务", 40.0)]  # 漏提50, 恰凑满100
        outcome = check_business_closure(under, {}, document_id=1, expected_per_lot=150.0)
        assert outcome.validation.kind != ScoringValidationKind.COMPLETE

        full = [_item("技术", 60.0), _item("商务", 40.0), _item("价格", 50.0)]
        outcome = check_business_closure(full, {}, document_id=1, expected_per_lot=150.0)
        assert outcome.validation.kind == ScoringValidationKind.COMPLETE

    def test_scope_gap_metric_compares_on_scope_axis(self):
        """审查V3: 多标段部分闭合候选按 scope 轴优于现状, 不再被单包轴误拒。"""
        from app.core.extractor.scoring.vlm.rescue_rounds import (
            _scope_gap_metric,
        )

        current = [
            SimpleNamespace(scheme=None, lot="标段三", score_weight=55.0),
            SimpleNamespace(scheme=None, lot="标段四", score_weight=100.0),
        ]
        candidate = [
            SimpleNamespace(scheme=None, lot="标段三", score_weight=90.0),
            SimpleNamespace(scheme=None, lot="标段四", score_weight=100.0),
        ]
        # 现状: 标段三缺45 → (1, 45); 候选: 标段三缺10 → (1, 10) 更优
        assert _scope_gap_metric(candidate, 100.0) < _scope_gap_metric(current, 100.0)
        # 空集恒最差
        assert _scope_gap_metric([], 100.0) > _scope_gap_metric(current, 100.0)

    def test_anchoring_normalizes_fullwidth_text(self):
        """审查V4: 文本层全角数字/字母/括号经 NFKC 归一后不得误拒真实项。"""
        from app.core.extractor.scoring.vlm.candidate_gates import (
            filter_anchored_items,
        )
        from app.core.extractor.scoring.vlm.rescue_input_builder import (
            _normalize_page_texts,
        )

        anchor = _normalize_page_texts(["磋商报价评审（Ｆ１） 满分２０分", "技术方案 15.0分"])
        items = [
            SimpleNamespace(score_item_name="磋商报价评审(F1)", score_weight=20.0),
            SimpleNamespace(score_item_name="技术方案", score_weight=15.0),
        ]
        kept, dropped = filter_anchored_items(items, anchor)
        assert len(kept) == 2 and dropped == []

    def test_multi_lot_candidates_with_lots_reach_complete(self):
        """双标段候选各自闭合100分且带 lot 时, 闭合裁判必须判 COMPLETE
 (修复后 doc2481 的期望终态)。"""
        from app.core.extractor.scoring.scoring_scope_contracts import (
            ScoringValidationKind,
        )
        from app.core.extractor.scoring.vlm.candidate_gates import (
            check_business_closure,
        )
        from app.schemas.requirement import ExtractedScoringItem

        def _item(name: str, score: float, lot: str) -> ExtractedScoringItem:
            return ExtractedScoringItem(
                category="TECH", score_item_name=name, description=f"{name}评分",
                score_weight=score, lot=lot, fact_type="base_score",
                score_rules=["规则"], deduction_rules=[], source_pages=[1],
            )

        items = [
            _item("技术响应三", 70.0, "标段三"), _item("报价三", 30.0, "标段三"),
            _item("技术响应四", 70.0, "标段四"), _item("报价四", 30.0, "标段四"),
        ]
        outcome = check_business_closure(items, {}, document_id=1)
        assert outcome.validation.kind == ScoringValidationKind.COMPLETE

    async def test_retry_feedback_flags_duplicated_names(self):
        """守恒反馈: 同名多行(父组与子项双计形态)必须在重询反馈中点名。"""
        from app.core.extractor.scoring.vlm.rescue_rounds import (
            _build_retry_feedback,
        )

        outcome = SimpleNamespace(feedback="核计110/100未闭合")
        kept = [
            SimpleNamespace(score_item_name="央行动态及政策研究功能"),
            SimpleNamespace(score_item_name="央行动态及政策研究功能"),
            SimpleNamespace(score_item_name="外汇功能"),
        ]
        feedback = _build_retry_feedback(outcome, kept, [])
        assert "央行动态及政策研究功能" in feedback
        assert "父组行禁止与其子项同时计分" in feedback
        assert "核计110/100未闭合" in feedback


class TestGates:
    def test_fabricated_score_rejected_by_anchoring(self):
        items = to_extracted_items(
            [VlmScoringItemDraft(score_item_name="投标文件格式", score_weight=10.0)]
        )
        outcome = check_evidence_anchoring(items, "投标文件格式应齐全无分值列")
        assert not outcome.passed
        assert "unanchored" in outcome.rejection_codes
        assert "10" in outcome.feedback

    def test_scan_page_without_text_layer_fails_safe(self):
        items = to_extracted_items(
            [VlmScoringItemDraft(score_item_name="技术方案", score_weight=40.0)]
        )
        outcome = check_evidence_anchoring(items, "")
        assert not outcome.passed
        assert "no_anchor_text" in outcome.rejection_codes

    def test_composite_parent_child_name_anchored_by_segments(self):
        items = to_extracted_items(
            [VlmScoringItemDraft(score_item_name="企业业绩-合同数量", score_weight=2.0)]
        )
        outcome = check_evidence_anchoring(
            items, "企业业绩其中合同数量每项2分"
        )
        assert outcome.passed

    def test_unclosed_total_is_soft_gate_not_hard_reject(self):
        items = to_extracted_items(
            [VlmScoringItemDraft(score_item_name="技术方案", score_weight=40.0)]
        )
        outcome = evaluate_candidate_gates(
            items, anchor_text="技术方案40分", volume_defs={}, document_id=1
        )
        assert outcome.passed
        assert "not_closed" in outcome.rejection_codes
        assert outcome.validation is not None
        assert outcome.validation.needs_review is True


class TestGroupLabelSanitization:
    """: 伪造分组标签(如照抄示例的"技术卷")必须在采纳前剥除。"""

    def test_fabricated_label_stripped_item_kept(self):
        from app.core.extractor.scoring.vlm.candidate_gates import (
            sanitize_unanchored_group_labels,
        )

        items = to_extracted_items(
            [
                VlmScoringItemDraft(
                    score_item_name="价格评审", score_weight=60.0,
                    group_path=["技术卷"], score_rules=["基准价法"],
                )
            ]
        )
        sanitized, stripped = sanitize_unanchored_group_labels(items, _GOOD_ANCHOR)
        assert sanitized[0].volume_label is None
        assert sanitized[0].score_item_name == "价格评审"
        assert sanitized[0].score_weight == 60.0
        assert stripped == ["价格评审: 技术卷"]

    def test_real_label_kept(self):
        from app.core.extractor.scoring.vlm.candidate_gates import (
            sanitize_unanchored_group_labels,
        )

        items = to_extracted_items(
            [
                VlmScoringItemDraft(
                    score_item_name="业绩案例", score_weight=20.0,
                    group_path=["综合实力和业绩经验"], score_rules=["每案例2分"],
                )
            ]
        )
        anchor = "2综合实力和业绩经验25分业绩案例20分"
        sanitized, stripped = sanitize_unanchored_group_labels(items, anchor)
        assert sanitized[0].volume_label == "综合实力和业绩经验"
        assert stripped == []

    def test_empty_anchor_passthrough(self):
        """空文本层不剥标签——整批由锚定门禁 no_anchor_text fail-closed 接管。"""
        from app.core.extractor.scoring.vlm.candidate_gates import (
            sanitize_unanchored_group_labels,
        )

        items = to_extracted_items(
            [
                VlmScoringItemDraft(
                    score_item_name="技术方案", score_weight=40.0,
                    group_path=["技术卷"],
                )
            ]
        )
        sanitized, stripped = sanitize_unanchored_group_labels(items, "")
        assert sanitized[0].volume_label == "技术卷"
        assert stripped == []

    def test_copy_failure_still_strips_fabricated_label(self):
        """复制异常不得让伪造标签穿透并制造“已剥除”假埋点。"""
        from app.core.extractor.scoring.vlm.candidate_gates import (
            sanitize_unanchored_group_labels,
        )

        class _BrokenModelCopy:
            score_item_name = "价格评审"
            score_weight = 60.0
            volume_label = "技术卷"

            def model_copy(self, **_kwargs):
                raise ValueError("copy failed")

        original = _BrokenModelCopy()
        sanitized, stripped = sanitize_unanchored_group_labels(
            [original],
            _GOOD_ANCHOR,
        )

        assert sanitized[0] is not original
        assert sanitized[0].volume_label is None
        assert stripped == ["价格评审: 技术卷"]

    async def test_fabricated_labels_stripped_before_adoption(self, monkeypatch):
        """引擎级: 伪造标签剥除后采纳, 落库项 volume_label 为空且埋点记录。"""
        tail = _make_tail(needs_review=True)
        client = _FakeVisionClient([_FABRICATED_LABEL_JSONL])
        _channel_env(
            monkeypatch, tail=tail,
            rescue_input=_stub_input(_GOOD_ANCHOR), client=client,
        )
        result = await _invoke(tail)
        assert result.adopted is True
        assert all(getattr(i, "volume_label", None) is None for i in result.items)
        assert tail.l3_diagnostics[-1]["stripped_group_labels"] == [2]
        assert "group_relation_rounds" not in tail.l3_diagnostics[-1]

    async def test_stripped_labels_named_in_retry_feedback(self, monkeypatch):
        """Goodhart回路瓦解: 下一轮重询必须点名被剥标签并禁止编造。"""
        from app.core.extractor.scoring.vlm import rescue_rounds

        tail = _make_tail(needs_review=True)
        client = _FakeVisionClient(
            [_FABRICATED_LABEL_JSONL, _FABRICATED_LABEL_JSONL]
        )
        _channel_env(
            monkeypatch, tail=tail,
            rescue_input=_stub_input(_GOOD_ANCHOR), client=client,
        )

        async def _always_amber(**kwargs):
            return ("amber", None)

        monkeypatch.setattr(rescue_rounds, "_judge_candidate_verdict", _always_amber)
        await _invoke(tail)
        assert client.calls >= 2
        assert "已被剥除" in client.prompts[1]
        assert "技术卷" in client.prompts[1]


class TestContract:
    def test_jsonl_truncation_only_loses_tail_line(self):
        text = (
            '{"type": "header", "declared_total": 100}\n'
            '{"type": "item", "score_item_name": "技术方案", "score_weight": 40}\n'
            '{"type": "item", "score_item_name": "被截断'
        )
        header, items, broken = extract_jsonl_records(text)
        assert header is not None
        assert len(items) == 1
        assert broken == 1

    def test_self_reference_in_group_path_not_suicidal(self):
        drafts = [
            VlmScoringItemDraft(
                score_item_name="类似业绩", score_weight=1.0,
                group_path=["技术商务部分", "类似业绩"],
            ),
            VlmScoringItemDraft(
                score_item_name="技术性能", score_weight=3.0,
                group_path=["技术商务部分", "技术性能"],
            ),
        ]
        assert len(normalize_vlm_items(drafts)) == 2

    def test_parent_group_row_dropped_via_children_paths(self):
        drafts = [
            VlmScoringItemDraft(
                score_item_name="项目管理大纲部分", score_weight=30.0,
                group_path=["资信标"],
            ),
            VlmScoringItemDraft(
                score_item_name="项目管理大纲编制", score_weight=5.0,
                group_path=["资信标", "项目管理大纲部分"],
            ),
        ]
        names = [d.score_item_name for d in normalize_vlm_items(drafts)]
        assert names == ["项目管理大纲编制"]

    def test_symmetric_factors_with_distinct_rules_not_deduped(self):
        # 同名同分但规则不同的对称因素是合法独立行
        drafts = [
            VlmScoringItemDraft(
                score_item_name="业绩", score_weight=3.0, score_rules=["近三年合同"]
            ),
            VlmScoringItemDraft(
                score_item_name="业绩", score_weight=3.0, score_rules=["中标通知书"]
            ),
        ]
        assert len(normalize_vlm_items(drafts)) == 2

    def test_composition_table_copy_dropped_when_detail_exists(self):
        drafts = [
            VlmScoringItemDraft(
                score_item_name="施工组织设计", score_weight=35.0,
                group_path=["第一个信封评分分值构成（总分100分）"],
            ),
            VlmScoringItemDraft(
                score_item_name="施工组织设计", score_weight=35.0,
                group_path=["详细评审"],
            ),
        ]
        kept = normalize_vlm_items(drafts)
        assert len(kept) == 1
        assert kept[0].group_path == ["详细评审"]

    def test_mapping_reuses_category_inference_and_lot_detection(self):
        items = to_extracted_items(
            [
                VlmScoringItemDraft(
                    score_item_name="类似项目业绩", score_weight=3.0,
                    group_path=["采购包2", "商务部分"],
                    score_rules=["每提供一个得1分"], source_page=5,
                )
            ]
        )
        item = items[0]
        assert item.category == "PERF"
        assert item.lot == "采购包2"
        assert item.volume_label == "商务部分"
        assert item.source_pages == [5]
        assert item.fact_type == "base_score"

    def test_volume_attributed_by_def_label_match(self):
        defs = {
            "commercial": SimpleNamespace(label="商务标", volume_max=20.0, weight=0.2),
            "technical": SimpleNamespace(label="技术标", volume_max=40.0, weight=0.4),
        }
        items = to_extracted_items(
            [
                VlmScoringItemDraft(
                    score_item_name="企业综合实力", score_weight=14.0,
                    group_path=["商务标评审标准"],
                )
            ],
            defs,
        )
        assert items[0].volume == "commercial"

    def test_volume_keyword_inference_only_when_in_declared_defs(self):
        # 路径含"技术"但声明卷中无 technical → 诚实留空, 防默认值错卷
        defs = {"price": SimpleNamespace(label="报价", volume_max=40.0, weight=0.4)}
        items = to_extracted_items(
            [
                VlmScoringItemDraft(
                    score_item_name="施工方案", score_weight=10.0,
                    group_path=["技术评分"],
                )
            ],
            defs,
        )
        assert items[0].volume is None

    def test_volume_none_without_defs(self):
        items = to_extracted_items(
            [VlmScoringItemDraft(score_item_name="技术方案", score_weight=40.0)]
        )
        assert items[0].volume is None


_PARTIAL_JSONL = (
    '{"type": "header", "declared_total": 100, "volume_totals": []}\n'
    '{"type": "item", "score_item_name": "技术方案", "score_weight": 40,'
    ' "group_path": [], "fact_type": "base_score", "score_rules": ["优良中差"], "source_page": 1}\n'
    '{"type": "item", "score_item_name": "商务评审", "score_weight": 50,'
    ' "group_path": [], "fact_type": "base_score", "score_rules": ["综合评审"], "source_page": 1}\n'
)
_PARTIAL_ANCHOR = "技术方案40分商务评审50分总分100分"
_WORSE_JSONL = (
    '{"type": "item", "score_item_name": "技术方案", "score_weight": 20,'
    ' "group_path": [], "fact_type": "base_score", "score_rules": ["差"], "source_page": 1}\n'
)
_MIXED_JSONL = _GOOD_JSONL + _FABRICATED_JSONL


class TestPerItemAnchoring:
    def test_keeps_anchored_and_drops_fabricated(self):
        items = to_extracted_items(
            [
                VlmScoringItemDraft(score_item_name="技术方案", score_weight=40.0),
                VlmScoringItemDraft(score_item_name="价格评审", score_weight=60.0),
                VlmScoringItemDraft(score_item_name="投标文件格式", score_weight=10.0),
            ]
        )
        kept, dropped = filter_anchored_items(items, _GOOD_ANCHOR)
        assert [item.score_item_name for item in kept] == ["技术方案", "价格评审"]
        assert len(dropped) == 1
        assert "投标文件格式" in dropped[0]

    def test_all_unanchored_keeps_none(self):
        items = to_extracted_items(
            [VlmScoringItemDraft(score_item_name="投标文件格式", score_weight=10.0)]
        )
        kept, dropped = filter_anchored_items(items, _GOOD_ANCHOR)
        assert kept == []
        assert dropped

    def test_page_text_anchor_accepts_missing_chunk_item(self):
        items = to_extracted_items(
            [
                VlmScoringItemDraft(score_item_name="技术参数响应", score_weight=24.0),
                VlmScoringItemDraft(score_item_name="自主知识产权", score_weight=3.0),
            ]
        )
        page_text = "技术参数响应24分类似业绩6分自主知识产权3分"
        kept, dropped = filter_anchored_items(items, page_text)
        assert len(kept) == 2
        assert dropped == []


class TestAdoptionStateMachine:
    async def test_mixed_batch_drops_fabricated_and_adopts_complete(self, monkeypatch):
        tail = _make_tail(needs_review=True)
        client = _FakeVisionClient([_MIXED_JSONL])
        _channel_env(
            monkeypatch, tail=tail,
            rescue_input=_stub_input(_GOOD_ANCHOR), client=client,
        )
        result = await _invoke(tail)
        assert result.adopted is True
        assert result.report.needs_review is False
        assert result.report.total == 100.0
        names = sorted(item.score_item_name for item in result.items)
        assert names == ["价格评审", "技术方案"]
        assert "投标文件格式" not in names

    async def test_unclosed_but_better_adopted_with_needs_review(self, monkeypatch):
        tail = _make_tail(needs_review=True)
        client = _FakeVisionClient([_PARTIAL_JSONL])
        _channel_env(
            monkeypatch, tail=tail,
            rescue_input=_stub_input(_PARTIAL_ANCHOR), client=client,
        )
        result = await _invoke(tail)
        assert result is not tail
        assert result.adopted is True
        assert result.report.needs_review is True
        assert result.report.total == 90.0
        assert tail.l3_diagnostics[-1]["status"] == "ok_needs_review"
        assert any("待人工核对" in line for line in result.diagnostics)

    async def test_false_green_current_adopts_anchored_partial_candidate(
        self, monkeypatch
    ):
        """ doc2478形态: 假绿现状(错误项恰凑满100, gap=0)是虚假优势,
 不得作为拒收基准——锚定通过的未闭合候选必须部分采纳并暴露真实缺口。"""
        tail = ScoringRescueIntegrationResult(
            items=[
                SimpleNamespace(score_item_name="评分因素权重", score_weight=80.0),
                SimpleNamespace(score_item_name="评标价", score_weight=20.0),
            ],
            report=SimpleNamespace(
                needs_review=False, total=100.0, expected_total=100.0, note="假绿"
            ),
            fidelity_sidecar={}, diagnostics=[], l3_diagnostics=[],
            adopted=False, mode="shadow",
        )
        client = _FakeVisionClient([_PARTIAL_JSONL])
        _channel_env(
            monkeypatch, tail=tail,
            rescue_input=_stub_input(_PARTIAL_ANCHOR), client=client,
        )
        result = await attempt_vlm_scoring_rescue(
            tail, db=object(), document_id=1, project_id=None,
            scoring_chunks=[SimpleNamespace(content=_PARTIAL_ANCHOR, page_num=1)],
            volume_defs={}, trigger_non_green=True,
        )
        assert result is not tail
        assert result.adopted is True
        assert result.report.needs_review is True
        assert result.report.total == 90.0
        assert tail.l3_diagnostics[-1]["false_green_current"] is True
        assert tail.l3_diagnostics[-1]["status"] == "ok_needs_review"

    async def test_pdf_adopted_complete_not_treated_as_false_green(
        self, monkeypatch
    ):
        """: PDF 已采纳 8 项/100 且 needs_review=False 时, verdict 仍非绿
 会续接 VLM; 不得把上一梯队闭合满分误判为假绿并直采更差的 90 分候选。"""
        pdf_items = [
            SimpleNamespace(
                score_item_name=f"评分项{idx}", score_weight=weight,
                scheme=None, lot=None,
            )
            for idx, weight in enumerate((15.0, 15.0, 15.0, 15.0, 10.0, 10.0, 10.0, 10.0), start=1)
        ]
        tail = ScoringRescueIntegrationResult(
            items=pdf_items,
            report=SimpleNamespace(
                needs_review=False, total=100.0, expected_total=100.0, note="PDF闭合"
            ),
            fidelity_sidecar={},
            diagnostics=["pdf_chat_rescue: PDF直读救援采纳8项, 总分100.0"],
            l3_diagnostics=[
                {
                    "channel": "pdf_chat_rescue",
                    "status": "ok",
                    "final_verdict": "amber",
                }
            ],
            adopted=True,
            mode="shadow",
        )
        client = _FakeVisionClient([_PARTIAL_JSONL])
        _channel_env(
            monkeypatch, tail=tail,
            rescue_input=_stub_input(_PARTIAL_ANCHOR), client=client,
        )
        result = await attempt_vlm_scoring_rescue(
            tail, db=object(), document_id=1, project_id=None,
            scoring_chunks=[SimpleNamespace(content=_PARTIAL_ANCHOR, page_num=1)],
            volume_defs={}, trigger_non_green=True,
        )
        assert result is tail
        assert [item.score_item_name for item in result.items] == [
            f"评分项{idx}" for idx in range(1, 9)
        ]
        assert result.report.total == 100.0
        vlm_meta = tail.l3_diagnostics[-1]
        assert vlm_meta["channel"] == "vlm_rescue"
        assert vlm_meta["status"] == "rejected"
        assert vlm_meta.get("false_green_current") is not True
        assert vlm_meta.get("reject_reason") == "not_better_than_current"

    def test_pdf_ladder_adopted_predicate_only_ok_statuses(self):
        from app.core.extractor.scoring.vlm.vlm_rescue_channel import (
            _pdf_ladder_already_adopted,
        )

        def _tail(status: str | None, channel: str = "pdf_chat_rescue"):
            diag = [] if status is None else [{"channel": channel, "status": status}]
            return SimpleNamespace(l3_diagnostics=diag)

        assert _pdf_ladder_already_adopted(_tail("ok")) is True
        assert _pdf_ladder_already_adopted(_tail("ok_needs_review")) is True
        assert _pdf_ladder_already_adopted(_tail("rejected")) is False
        assert _pdf_ladder_already_adopted(_tail("skipped")) is False
        assert _pdf_ladder_already_adopted(_tail("ok", channel="vlm_rescue")) is False
        assert _pdf_ladder_already_adopted(_tail(None)) is False

    async def test_false_green_current_still_rejects_unanchored_candidate(
        self, monkeypatch
    ):
        """假绿放宽仅豁免'优于现状'比较, 锚定红线不动摇: 全幻觉候选仍拒收。"""
        tail = ScoringRescueIntegrationResult(
            items=[SimpleNamespace(score_item_name="评分因素权重", score_weight=100.0)],
            report=SimpleNamespace(
                needs_review=False, total=100.0, expected_total=100.0, note="假绿"
            ),
            fidelity_sidecar={}, diagnostics=[], l3_diagnostics=[],
            adopted=False, mode="shadow",
        )
        client = _FakeVisionClient([_FABRICATED_JSONL])
        _channel_env(
            monkeypatch, tail=tail,
            rescue_input=_stub_input(_GOOD_ANCHOR), client=client,
        )
        result = await attempt_vlm_scoring_rescue(
            tail, db=object(), document_id=1, project_id=None,
            scoring_chunks=[SimpleNamespace(content=_GOOD_ANCHOR, page_num=1)],
            volume_defs={}, trigger_non_green=True,
        )
        assert result is tail
        assert tail.l3_diagnostics[-1]["status"] == "rejected"

    async def test_worse_than_current_keeps_status_quo(self, monkeypatch):
        tail = _make_tail(needs_review=True)
        original_items = list(tail.items)
        client = _FakeVisionClient([_WORSE_JSONL])
        _channel_env(
            monkeypatch, tail=tail,
            rescue_input=_stub_input("技术方案20分"), client=client,
        )
        result = await _invoke(tail)
        assert result is tail
        assert result.items == original_items
        assert tail.l3_diagnostics[-1]["status"] == "rejected"
        assert tail.l3_diagnostics[-1].get("reject_reason") == "not_better_than_current"

    async def test_existing_volume_defs_reattribute_price_to_complete(self, monkeypatch):
        """确定性卷定义存在时, 无 volume 的「价格评审」须归卷, 否则 100 分仍 needs_review。"""
        from app.core.extractor.scoring.volume_parser import VolumeDefinition

        defs = {
            "price": VolumeDefinition(
                volume="price", weight=0.6, volume_max=60.0, label="价格评审",
                source="framework",
            ),
            "technical": VolumeDefinition(
                volume="technical", weight=0.4, volume_max=40.0, label="技术方案",
                source="framework",
            ),
        }
        tail = _make_tail(needs_review=True)
        client = _FakeVisionClient([_GOOD_JSONL])
        _channel_env(
            monkeypatch, tail=tail,
            rescue_input=_stub_input(_GOOD_ANCHOR), client=client,
        )
        result = await attempt_vlm_scoring_rescue(
            tail, db=object(), document_id=1, project_id=None,
            scoring_chunks=[SimpleNamespace(content=_GOOD_ANCHOR, page_num=1)],
            volume_defs=defs,
        )
        assert result.adopted is True
        assert result.report.needs_review is False
        assert result.report.total == 100.0
        by_name = {item.score_item_name: item.volume for item in result.items}
        assert by_name["价格评审"] == "price"
        assert by_name["技术方案"] == "technical"
        assert tail.l3_diagnostics[-1]["status"] == "ok"

    async def test_zero_chunk_still_runs_when_input_available(self, monkeypatch):
        tail = _make_tail(needs_review=True)
        client = _FakeVisionClient([_GOOD_JSONL])
        _channel_env(
            monkeypatch, tail=tail,
            rescue_input=_stub_input(_GOOD_ANCHOR), client=client,
        )
        result = await attempt_vlm_scoring_rescue(
            tail, db=object(), document_id=1, project_id=None,
            scoring_chunks=[], volume_defs={},
        )
        assert result.adopted is True
        assert result.report.total == 100.0

    async def test_failed_relation_authority_is_not_repopulated_by_header_defs(
        self, monkeypatch
    ):
        """: fail-closed清空后，卷定义不得把未验证标签重新贴回。"""
        candidate = (
            '{"type": "header", "declared_total": 100, "volume_totals": ['
            '{"label": "技术卷", "total": 40, "weight": 0.4},'
            '{"label": "价格卷", "total": 60, "weight": 0.6}]}\n'
            '{"type": "item", "score_item_name": "技术方案", "score_weight": 40,'
            ' "group_path": ["技术卷"], "fact_type": "base_score",'
            ' "score_rules": ["优良中差"], "source_page": 1}\n'
            '{"type": "item", "score_item_name": "价格评审", "score_weight": 60,'
            ' "group_path": ["价格卷"], "fact_type": "price_formula",'
            ' "score_rules": ["基准价法"], "source_page": 1}\n'
        )
        anchor = "技术卷40分技术方案40分价格卷60分价格评审60分总分100分"
        tail = _make_tail(needs_review=True)
        client = _FakeVisionClient([candidate])
        _channel_env(
            monkeypatch,
            tail=tail,
            rescue_input=_stub_input(anchor),
            client=client,
        )

        result = await attempt_vlm_scoring_rescue(
            tail,
            db=object(),
            document_id=1,
            project_id=None,
            scoring_chunks=[],
            volume_defs={},
        )

        assert result.adopted is True
        assert all(item.volume_label is None for item in result.items)
        assert all(item.volume is None for item in result.items)
        relation = tail.l3_diagnostics[-1]["group_relation_rounds"][0]
        assert relation["authoritative"] is False
        assert relation["cleared"] == 2

    async def test_zero_chunk_applies_authoritative_group_relation_rows(self, monkeypatch):
        """: 零评分chunk的双通道轮次必须消费普通表格行关系证据。"""
        from app.core.extractor.scoring import group_relation_loader

        candidate = (
            '{"type": "header", "declared_total": 100, "volume_totals": []}\n'
            '{"type": "item", "score_item_name": "技术方案", "score_weight": 40,'
            ' "group_path": ["技术部分"], "fact_type": "base_score",'
            ' "score_rules": ["优良中差"], "source_page": 1}\n'
            '{"type": "item", "score_item_name": "类似业绩", "score_weight": 20,'
            ' "group_path": ["技术部分"], "fact_type": "base_score",'
            ' "score_rules": ["每项计分"], "source_page": 1}\n'
            '{"type": "item", "score_item_name": "价格评审", "score_weight": 40,'
            ' "group_path": ["价格部分"], "fact_type": "price_formula",'
            ' "score_rules": ["基准价法"], "source_page": 1}\n'
        )
        anchor = "技术部分60分技术方案40分类似业绩20分价格部分40分价格评审40分"
        tail = _make_tail(needs_review=True)
        client = _FakeVisionClient([candidate])
        _channel_env(
            monkeypatch, tail=tail,
            rescue_input=_stub_input(anchor), client=client,
        )

        async def _relation_rows(*_args, **_kwargs):
            return [
                SimpleNamespace(
                    chunk_index=1,
                    page_num=1,
                    content="表格第1行：1 | 技术部分 60分 | 技术方案 40分 | 优良中差",
                ),
                SimpleNamespace(
                    chunk_index=2,
                    page_num=1,
                    content="表格第2行：类似业绩 20分 | 每项计分",
                ),
                SimpleNamespace(
                    chunk_index=3,
                    page_num=1,
                    content="表格第3行：2 | 价格评审 40分 | 基准价法",
                ),
            ]

        monkeypatch.setattr(
            group_relation_loader,
            "load_group_relation_rows",
            _relation_rows,
        )
        result = await attempt_vlm_scoring_rescue(
            tail,
            db=object(),
            document_id=1,
            project_id=None,
            scoring_chunks=[],
            volume_defs={},
        )

        assert result.adopted is True
        assert {
            item.score_item_name: item.volume_label for item in result.items
        } == {
            "技术方案": "技术部分",
            "类似业绩": "技术部分",
            "价格评审": "价格评审",
        }
        assert tail.l3_diagnostics[-1]["group_relation_rounds"][0] == {
            "authoritative": True,
            "corrected": 1,
            "cleared": 0,
            "reason": "",
        }

    async def test_relation_totals_supersede_header_volume_defs(self, monkeypatch):
        """: 权威关系存在时，模型header卷总分整轴旁路，卷定义取闭合分值。

 跨页组的真实总分只有结构缝合能拿到；header按单页视野谎报的总分
 （数字恰在页文本他处出现）不得再进入卷基准。
 """
        from app.core.extractor.scoring import group_relation_loader

        candidate = (
            '{"type": "header", "declared_total": 100, "volume_totals": ['
            '{"label": "技术部分", "total": 30},'
            '{"label": "价格评审", "total": 70}]}\n'
            '{"type": "item", "score_item_name": "技术方案", "score_weight": 40,'
            ' "group_path": ["技术部分"], "fact_type": "base_score",'
            ' "score_rules": ["优良中差"], "source_page": 1}\n'
            '{"type": "item", "score_item_name": "类似业绩", "score_weight": 20,'
            ' "group_path": ["技术部分"], "fact_type": "base_score",'
            ' "score_rules": ["每项计分"], "source_page": 1}\n'
            '{"type": "item", "score_item_name": "价格评审", "score_weight": 40,'
            ' "group_path": ["价格评审"], "fact_type": "price_formula",'
            ' "score_rules": ["基准价法"], "source_page": 1}\n'
        )
        # 锚文本刻意含"30""70"字样：旧的子串锚定会放行header谎报值
        anchor = "技术部分60分技术方案40分类似业绩20分价格评审40分第30页共70页"
        tail = _make_tail(needs_review=True)
        client = _FakeVisionClient([candidate])
        _channel_env(
            monkeypatch, tail=tail,
            rescue_input=_stub_input(anchor), client=client,
        )

        async def _relation_rows(*_args, **_kwargs):
            return [
                SimpleNamespace(
                    chunk_index=1,
                    page_num=1,
                    content="表格第1行：1 | 技术部分 60分 | 技术方案 40分 | 优良中差",
                ),
                SimpleNamespace(
                    chunk_index=2,
                    page_num=1,
                    content="表格第2行：类似业绩 20分 | 每项计分",
                ),
                SimpleNamespace(
                    chunk_index=3,
                    page_num=1,
                    content="表格第3行：2 | 价格评审 40分 | 基准价法",
                ),
            ]

        monkeypatch.setattr(
            group_relation_loader,
            "load_group_relation_rows",
            _relation_rows,
        )
        result = await attempt_vlm_scoring_rescue(
            tail,
            db=object(),
            document_id=1,
            project_id=None,
            scoring_chunks=[],
            volume_defs={},
        )

        assert result.adopted is True
        by_name = {item.score_item_name: item for item in result.items}
        assert by_name["技术方案"].volume == "technical"
        assert by_name["技术方案"].volume_max == 60.0
        assert by_name["技术方案"].volume_weight == 0.6
        assert by_name["类似业绩"].volume_max == 60.0
        assert by_name["价格评审"].volume == "price"
        assert by_name["价格评审"].volume_max == 40.0
        meta = tail.l3_diagnostics[-1]
        assert meta.get("volume_defs_from_relation") is True
        assert "volume_defs_from_header" not in meta


class TestPageLocatorAndWindow:
    def test_locator_requires_header_and_score_signal(self):
        pages = locate_scoring_pages(
            {
                1: "公开招标文件 项目名称 预算金额90万元",
                5: "评标方法综合评分法 序号 评分项 权重(%) 价格30 技术部分54分 评分因素 分值",
            }
        )
        assert 5 in pages
        assert 1 not in pages

    def test_locator_empty_when_no_hit(self):
        assert locate_scoring_pages({1: "资格性审查表 营业执照"}) == set()

    def test_collect_rescue_pages_unions_and_expands(self):
        pages, truncated = collect_rescue_pages(
            {5, 6, 7, 8, 9},
            {5, 10},
            page_margin=1,
            max_pages=30,
            page_count=10,
        )
        assert pages == [4, 5, 6, 7, 8, 9, 10]
        assert truncated is False

    def test_collect_rescue_pages_empty_when_no_signals(self):
        pages, truncated = collect_rescue_pages(
            set(), set(), page_margin=1, max_pages=30, page_count=10
        )
        assert pages == []
        assert truncated is False

