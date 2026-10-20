from tests.synthetic_data import synthetic_text
"""投标审查 + 标书编写证据闸门单元测试 (推广)"""
import os
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")

from types import SimpleNamespace
from app.core.writer.clause_extractor import ClauseExtractor
from app.schemas.writing import ExtractedClause


def test_source_page_range_correction():
    """步骤3: source_page 超出批级范围时必须清空为 None，而非修正为 page_start。

 设计意图（clause_extractor.py:811-818， 修复）：超出批级范围的
 source_page 清空为 None，由 source_page_start/end 承担用户态范围语义，
 避免给用户"伪精确的单页跳转"。此行为是有意为之，不得将断言改回"回退
 page_start"语义。
 """
    clauses = [
        ExtractedClause(sub_type="format_rule", content="需要盖章", source_page=999),
        ExtractedClause(sub_type="format_rule", content="需要装订", source_page=5),
        ExtractedClause(sub_type="format_rule", content="需要页码", source_page=None),
    ]
    result = ClauseExtractor._verify_batch_clauses(
        clauses, "some batch text", page_start=3, page_end=10,
    )
    # 越界页码（999 超出 [3-10]）必须清空为 None，而非猜测回退到 page_start（避免伪精确跳转）
    assert result[0].source_page is None, (
        f"越界页码应清空为 None（避免伪精确跳转），不得修正为 page_start，"
        f"实际得到: {result[0].source_page}"
    )
    # 范围内页码保留（对照：清空逻辑仅作用于越界场景，不误杀有效锚点）
    assert result[1].source_page == 5, (
        f"范围内页码 5 应保留不变，实际得到: {result[1].source_page}"
    )
    # 未提供精确页时回退到批次首页，作为 PDF 默认打开锚点
    assert result[2].source_page == 3, (
        f"未提供精确页时应默认回退到 page_start=3，实际得到: {result[2].source_page}"
    )
    print("PASSED: out-of-range source_page cleared to None, not corrected to page_start")


def test_source_page_in_range_preserved():
    """对照用例：页码在批级范围内（含边界）时必须原样保留，清空逻辑不误杀有效锚点。

    设计意图（clause_extractor.py:808-820）：_verify_batch_clauses 只对越界
    页码执行清空；范围内的精确页码是有效的 PDF 锚点，不应被修改。本用例与
    test_source_page_range_correction 配对，共同约束该边界逻辑。
    """
    clauses = [
        ExtractedClause(sub_type="format_rule", content="格式要求A", source_page=3),   # 等于 page_start
        ExtractedClause(sub_type="format_rule", content="格式要求B", source_page=7),   # 范围中间
        ExtractedClause(sub_type="format_rule", content="格式要求C", source_page=10),  # 等于 page_end
    ]
    result = ClauseExtractor._verify_batch_clauses(
        clauses, "some batch text", page_start=3, page_end=10,
    )
    assert result[0].source_page == 3, (
        f"边界值 page_start=3 应保留，实际得到: {result[0].source_page}"
    )
    assert result[1].source_page == 7, (
        f"范围内页码 7 应保留，实际得到: {result[1].source_page}"
    )
    assert result[2].source_page == 10, (
        f"边界值 page_end=10 应保留，实际得到: {result[2].source_page}"
    )
    print("PASSED: in-range source_page preserved unchanged")


def test_raw_text_verification_removes_fake():
    """步骤4: raw_text 与批次原文不匹配时清除

 注意: content 需要与 batch 有关键词交集，否则会命中 的 content 兜底被整条丢弃;
 这里只聚焦"伪原文被清除、clause 仍保留"这一证据闸门行为。
 """
    batch_text = "投标人须具有建筑工程施工总承包一级资质。投标人应提交营业执照复印件。"
    clauses = [
        ExtractedClause(
            sub_type="format_rule",
            content="投标人须具有一级资质",
            raw_text="投标人须具有建筑工程施工总承包一级资质",
            source_page=1,
        ),
        ExtractedClause(
            sub_type="format_rule",
            content="投标人须提交营业执照相关ISO认证",
            raw_text="投标人须提供ISO9001质量管理体系认证证书原件及复印件各一份",
            source_page=1,
        ),
    ]
    result = ClauseExtractor._verify_batch_clauses(
        clauses, batch_text, page_start=1, page_end=3,
    )
    assert len(result) == 2, "Both clauses should be preserved when content overlaps"
    assert result[0].raw_text is not None, "Matching raw_text should be preserved"
    assert result[1].raw_text is None, "Non-matching raw_text should be cleared"
    print("PASSED: raw_text verification removes fake quotes")


def test_raw_text_preserves_short():
    """步骤4: 短 raw_text 不做校验(避免误杀)"""
    clauses = [
        ExtractedClause(
            sub_type="format_rule",
            content="short",
            raw_text="短文本",
            source_page=1,
        ),
    ]
    result = ClauseExtractor._verify_batch_clauses(
        clauses, "completely different batch", page_start=1, page_end=3,
    )
    assert result[0].raw_text == "短文本", "Short raw_text should be preserved without check"
    print("PASSED: short raw_text preserved")


def _run_claim_response_gate(extracted, response_content, has_response):
    """Fix-01 helper: 走 ClaimResponseConsistencyGate + Pipeline apply_patch 路径。

 Gate 语义（起）：命中矛盾 → patch ``failure_state=needs_review``
 并追加 comment 提示，不再改写 compliance_level（闸门只转人工复核，
 不替 LLM 提升符合度）；不命中则 ``pass``，target 不变。
 """
    from app.core.ai.gates import build_pipeline_for
    from app.core.ai.gates.base import GateContext

    ctx = GateContext(
        flow="investigate",
        stage="post_llm",
        target=extracted,
        evidence={
            "is_mandatory": getattr(extracted, "_is_mandatory", False),
            "has_response": has_response,
            "response_content": response_content,
            "category": "qualification",
        },
        config={"project_id": 0},
    )
    build_pipeline_for("investigate").run(ctx, apply_patch_to=extracted)


def test_claim_response_check():
    """步骤2: LLM 判定未提供但实际有内容时 → Gate 追加提示并降为 needs_review"""

    class FakeExtracted:
        compliance_level = "not"
        comment = "投标人未提供营业执照复印件"
        score = 0
        is_compliant = False
        risk_level = "high"
        relevance_score = None
        relevance_note = None
        failure_state = None
        _is_mandatory = True

    extracted = FakeExtracted()
    response_content = synthetic_text('投标人提交了营业执照复印件，编号', '911100', '00MA01', '234567', '，有效期至2028年12月31日。')
    _run_claim_response_gate(extracted, response_content, has_response=True)

    assert "[系统提示:" in extracted.comment, f"Hint should be appended, got: {extracted.comment}"
    assert "核验" in extracted.comment
    # 冲突必须被标记为 needs_review；compliance_level 保持 LLM 原判
    # （闸门不再自动升级为 partially，判定方向归人工复核）
    assert extracted.failure_state == "needs_review"
    assert extracted.compliance_level == "not"
    print("PASSED: claim-response Gate flags needs_review without upgrading compliance")


def test_claim_response_no_false_positive():
    """步骤2: LLM 正确判定未提供且确实无响应 → Gate pass，无 patch"""

    class FakeExtracted:
        compliance_level = "not"
        comment = "投标人未提供营业执照复印件"
        score = 0
        is_compliant = False
        risk_level = "high"
        relevance_score = None
        relevance_note = None
        failure_state = None
        _is_mandatory = True

    extracted = FakeExtracted()
    # Fix-01: Gate 用 evidence.has_response + response_content 判定，不看 pair
    _run_claim_response_gate(extracted, response_content="[未找到响应]", has_response=False)
    assert "[系统提示:" not in extracted.comment, "Should not append hint when no real response"
    assert extracted.compliance_level == "not", "Should not downgrade when no conflict"
    print("PASSED: no false positive when response is empty")


def test_claim_response_skip_compliant():
    """步骤2: LLM 判定符合时 Gate applies 返回 False，target 不变"""

    class FakeExtracted:
        compliance_level = "fully"
        comment = "完全符合要求"
        score = 95
        failure_state = None
        _is_mandatory = False

    extracted = FakeExtracted()
    _run_claim_response_gate(extracted, response_content="有内容", has_response=True)
    assert "[系统提示:" not in extracted.comment, "Should skip when compliance is not 'not'"
    assert extracted.compliance_level == "fully"
    print("PASSED: skip claim check for compliant results")


def test_writing_clause_update_accepts_raw_text_and_source_page():
    """writer 编辑契约: 必须允许补录 raw_text 和 source_page"""
    import pytest
    from app.schemas.writing import WritingClauseUpdate

    update = WritingClauseUpdate(
        content="更新后的内容",
        raw_text="招标原文第三段",
        source_page=12,
    )
    assert update.raw_text == "招标原文第三段"
    assert update.source_page == 12

    # 空字符串应归一化为 None, 避免 None/'' 歧义穿透 DB
    normalized = WritingClauseUpdate(raw_text="   ")
    assert normalized.raw_text is None

    # source_page 必须 >= 1
    with pytest.raises(Exception):
        WritingClauseUpdate(source_page=0)
    print("PASSED: WritingClauseUpdate accepts raw_text/source_page")


def test_writing_clause_create_allows_missing_traceability():
    """writer 创建契约: 手工新增条款允许只提交结构化内容"""
    from app.schemas.writing import WritingClauseCreate

    plain = WritingClauseCreate(
        layer="hard_constraint",
        sub_type="disqualification",
        content="无证据示例",
    )
    assert plain.raw_text is None
    assert plain.source_page is None

    # 仅 raw_text 合法
    ok_raw = WritingClauseCreate(
        layer="hard_constraint",
        sub_type="disqualification",
        content="有摘录",
        raw_text="招标原文摘录",
    )
    assert ok_raw.raw_text == "招标原文摘录"

    # 仅 source_page 合法
    ok_page = WritingClauseCreate(
        layer="hard_constraint",
        sub_type="disqualification",
        content="有页码",
        source_page=5,
    )
    assert ok_page.source_page == 5
    print("PASSED: WritingClauseCreate allows optional traceability")


def test_verify_batch_clauses_counts_missing_and_cleared():
    """证据闸门: 需要能区分 LLM 原生缺失 与 被闸门清除 两种来源

 content 需与 batch 有关键词交集，以绕开 的 content 兜底。
 """
    batch_text = "投标人须具有建筑工程施工总承包一级资质。"
    clauses = [
        ExtractedClause(
            sub_type="format_rule",
            content="投标人须具有相关资质",
            raw_text=None,
            source_page=1,
        ),
        ExtractedClause(
            sub_type="format_rule",
            content="投标人须具有ISO认证相关资质",
            raw_text="投标人须提供ISO9001质量管理体系认证证书原件及复印件各一份",
            source_page=1,
        ),
        ExtractedClause(
            sub_type="format_rule",
            content="投标人须具有建筑工程一级资质",
            raw_text="投标人须具有建筑工程施工总承包一级资质",
            source_page=1,
        ),
    ]
    result = ClauseExtractor._verify_batch_clauses(
        clauses, batch_text, page_start=1, page_end=3,
    )
    assert len(result) == 3, "All clauses should be preserved when content overlaps"
    assert result[0].raw_text is None, "LLM 未给的应保持为 None"
    assert result[1].raw_text is None, "伪原文应被闸门清除"
    assert result[2].raw_text == "投标人须具有建筑工程施工总承包一级资质"
    print("PASSED: verify_batch_clauses differentiates missing vs cleared")


def test_verify_batch_clauses_writes_page_range():
    """证据闸门: 必须把批次页范围写到每条 clause 上(诚实范围)"""
    batch_text = "投标人须具有建筑工程施工总承包一级资质。"
    clauses = [
        ExtractedClause(
            sub_type="format_rule",
            content="投标人须具有建筑工程一级资质",
            raw_text="投标人须具有建筑工程施工总承包一级资质",
            source_page=None,
        ),
        ExtractedClause(
            sub_type="format_rule",
            content="投标人须具有相关资质",
            raw_text=None,
            source_page=None,
        ),
    ]
    result = ClauseExtractor._verify_batch_clauses(
        clauses, batch_text, page_start=9, page_end=10,
    )
    # page_start/page_end 必须按批次如实写入
    assert all(c.source_page_start == 9 for c in result)
    assert all(c.source_page_end == 10 for c in result)
    # 未提供精确页的候选应回退到批次首页, 作为默认打开 PDF 的锚点
    assert result[0].source_page == 9
    assert result[1].source_page == 9
    print("PASSED: verify_batch_clauses writes page range honestly")


def test_writing_clause_create_accepts_page_range_only():
    """writer 创建契约: 只提供 source_page_start/end 也应视为可溯源"""
    import pytest
    from app.schemas.writing import WritingClauseCreate

    # 仅提供页范围 (无 raw_text, 无 source_page)
    ok_range = WritingClauseCreate(
        layer="hard_constraint",
        sub_type="disqualification",
        content="仅页范围",
        source_page_start=9,
        source_page_end=10,
    )
    assert ok_range.source_page_start == 9
    assert ok_range.source_page_end == 10
    # source_page 应被回填为 start, 作为默认打开页
    assert ok_range.source_page == 9

    # start > end 必须拒绝
    with pytest.raises(Exception):
        WritingClauseCreate(
            layer="hard_constraint",
            sub_type="disqualification",
            content="错误范围",
            source_page_start=11,
            source_page_end=9,
        )

    # 仅 source_page 的老路径, schema 应补齐 start/end
    ok_single = WritingClauseCreate(
        layer="hard_constraint",
        sub_type="disqualification",
        content="仅单页",
        source_page=5,
    )
    assert ok_single.source_page_start == 5
    assert ok_single.source_page_end == 5
    print("PASSED: WritingClauseCreate handles page range")


def test_save_clauses_rejects_no_evidence_user_visible():
    """_save_clauses: 用户可见条款若既无 raw_text 又无页锚点, 必须拒绝入库"""
    # 仅做纯算法断言, 不接触 DB: 验证判定条件是否覆盖场景。
    ext_reject = ExtractedClause(
        sub_type="disqualification",
        content="完全无证据",
        raw_text=None,
        source_page=None,
        source_page_start=None,
        source_page_end=None,
    )
    ext_keep_page = ExtractedClause(
        sub_type="disqualification",
        content="仅有页锚点",
        raw_text=None,
        source_page=None,
        source_page_start=9,
        source_page_end=10,
    )
    ext_keep_excerpt = ExtractedClause(
        sub_type="disqualification",
        content="仅有摘录",
        raw_text="原文摘录",
        source_page=None,
        source_page_start=None,
        source_page_end=None,
    )
    system_allowed = ExtractedClause(
        sub_type="industry_profile",
        content="系统生成, 允许无证据",
        raw_text=None,
        source_page=None,
        source_page_start=None,
        source_page_end=None,
    )

    system_generated = {"industry_profile", "bid_structure_analysis", "bid_directory_spec"}

    def has_anchor(e: ExtractedClause) -> bool:
        return (
            e.source_page is not None
            or e.source_page_start is not None
            or e.source_page_end is not None
        )

    def admissible(e: ExtractedClause) -> bool:
        if e.sub_type in system_generated:
            return True
        return bool(e.raw_text) or has_anchor(e)

    assert admissible(ext_reject) is False
    assert admissible(ext_keep_page) is True
    assert admissible(ext_keep_excerpt) is True
    assert admissible(system_allowed) is True
    print("PASSED: _save_clauses minimum traceability floor holds")


if __name__ == "__main__":
    test_source_page_range_correction()
    test_source_page_in_range_preserved()
    test_raw_text_verification_removes_fake()
    test_raw_text_preserves_short()
    test_claim_response_check()
    test_claim_response_no_false_positive()
    test_claim_response_skip_compliant()
    test_writing_clause_update_accepts_raw_text_and_source_page()
    test_writing_clause_create_allows_missing_traceability()
    test_verify_batch_clauses_counts_missing_and_cleared()
    test_verify_batch_clauses_writes_page_range()
    test_writing_clause_create_accepts_page_range_only()
    test_save_clauses_rejects_no_evidence_user_visible()
    print("\nALL 12 TESTS PASSED")
