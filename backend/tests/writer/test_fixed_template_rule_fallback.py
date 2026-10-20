"""固定模板规则兜底 + LLM 输出归一化 防御测试。

覆盖:
- catalog 在 LLM 主路径完全空白时启用规则兜底, source=rule_only
- LLM 返回 wrapper / 裸 dict 形态时被归一化, 不触发兜底
- rule_fallback 严格在 SectionRange 内扫描, range 外 chunk 不被纳入
- rule_fallback entry 的 volume_type 从 SectionRange 继承, 不再做文本启发式
- 兜底 entry 的 snapshot 携带 is_complete_candidate=False, 触发下游 needs_review
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.writer.fixed_template.catalog import build_fixed_template_catalog_async
from app.core.writer.fixed_template.llm_recognizer import _normalize_recognize_response
from app.core.writer.fixed_template.rule_fallback import build_rule_fallback_entries
from app.core.writer.fixed_template.section_locator import SectionRange


def _chunk(page: int, text: str, idx: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        page_num=page,
        chunk_index=idx if idx is not None else page,
        content=text,
    )


def _template_chunks() -> list[SimpleNamespace]:
    """构造一个含投标函 + 保证金两条模板的"投标文件格式"范围。"""
    return [
        _chunk(38, "第五章 投标文件格式\n封面及目录格式"),
        _chunk(39, "投 标 文 件\n技术标\n投 标 人: (盖单位章)"),
        _chunk(
            41,
            "一、投标函\n"
            "致:招标人\n"
            "本投标人愿意按招标文件要求参加投标,我方承诺信息真实有效。\n"
            "投 标 人: (盖单位章)\n"
            "签字: 日 期: 年 月 日",
        ),
        _chunk(
            43,
            "三、投标保证金缴纳证明\n"
            "致:招标人:\n"
            "本投标人按招标文件要求自愿缴纳投标保证金。\n"
            "投 标 人: (盖单位章)\n"
            "日 期: 年 月 日",
        ),
    ]


# ---------------------------------------------------------------------------
# rule_fallback 直接调用
# ---------------------------------------------------------------------------


def test_rule_fallback_extracts_basic_templates_within_range() -> None:
    """SectionRange 内的标题应被识别为兜底 entry, 全部 source=rule_only。"""
    chunks = _template_chunks()
    ranges = [SectionRange(page_start=38, page_end=43, volume_type="technical")]

    entries = build_rule_fallback_entries(chunks, ranges, min_page=1, max_page=100)

    keys = {entry.template_key for entry in entries}
    assert keys, "兜底必须命中至少一个模板"
    assert "bid_letter" in keys or "bid_security_certificate" in keys
    for entry in entries:
        assert entry.source == "rule_only"
        assert entry.confidence < 0.75
        assert entry.volume_type == "technical"
        assert entry.is_complete_candidate is False


def test_rule_fallback_respects_range_boundary() -> None:
    """SectionRange 外的 chunk 即便含模板标题也不应被纳入兜底。"""
    chunks = _template_chunks() + [
        _chunk(
            60,
            "六、法定代表人授权委托书\n"
            "本人系企业法定代表人,特授权代表办理投标事务。\n"
            "投标人(公章)\n日 期: 年 月 日",
        ),
    ]
    ranges = [SectionRange(page_start=38, page_end=45, volume_type="technical")]

    entries = build_rule_fallback_entries(chunks, ranges, min_page=1, max_page=100)

    keys = {entry.template_key for entry in entries}
    assert "legal_representative_authorization" not in keys, "60 页在 range 外, 不应被纳入"


def test_rule_fallback_volume_type_inheritance() -> None:
    """兜底 entry 的 volume_type 应该直接继承自 SectionRange, 不再做文本启发式。"""
    chunks = _template_chunks()
    ranges = [SectionRange(page_start=38, page_end=43, volume_type="commercial")]

    entries = build_rule_fallback_entries(chunks, ranges, min_page=1, max_page=100)

    assert entries, "兜底应命中"
    for entry in entries:
        assert entry.volume_type == "commercial", (
            "即使原文含技术标字样, volume_type 也应来自 SectionRange"
        )


def test_rule_fallback_skips_directory_only_text() -> None:
    """目录页(全部 '一、xxx' 编号且无签章槽)不应被兜底输出。"""
    chunks = [
        _chunk(
            40,
            "目 录\n"
            "一、投标函\n二、投标保证金缴纳证明\n三、法定代表人证明书\n"
            "四、法定代表人授权委托书\n五、投标人基本情况表\n六、开标一览表",
        ),
    ]
    ranges = [SectionRange(page_start=40, page_end=40, volume_type=None)]

    entries = build_rule_fallback_entries(chunks, ranges, min_page=1, max_page=100)

    # 目录页只有标题行无签章,所有 segment 都太短或被 is_directory_like 拒
    assert all(entry.template_key != "bid_letter" or entry.signature_slots
               for entry in entries), "纯目录文本不应产出无签章 entry"


def test_rule_fallback_empty_chunks_returns_empty() -> None:
    assert build_rule_fallback_entries([], [], min_page=1, max_page=1) == []


# ---------------------------------------------------------------------------
# catalog 端到端: LLM 失败/空返 -> 兜底
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalog_fallback_when_llm_returns_empty(monkeypatch) -> None:
    """LLM 返回 [] 时 catalog 应走兜底而非返空。"""
    chunks = _template_chunks()

    async def _empty(*args, **kwargs):
        return []

    monkeypatch.setattr(
        "app.core.writer.fixed_template.catalog.recognize_templates",
        _empty,
    )

    catalog = await build_fixed_template_catalog_async(chunks, project_id=1)

    assert catalog
    assert all(entry.source == "rule_only" for entry in catalog)
    assert all(entry.snapshot_dict()["is_complete_candidate"] is False for entry in catalog)


@pytest.mark.asyncio
async def test_catalog_fallback_when_post_validator_drops_all(monkeypatch) -> None:
    """LLM 返回了内容但 post_validator 全部拒绝时也应走兜底。"""
    chunks = _template_chunks()

    async def _all_invalid(*args, **kwargs):
        # 模板 key 不在白名单 + title 也无法推断 -> post_validator 全拒
        return [
            {
                "template_key": "unknown_xxx",
                "title": "未知模板章节",
                "source_page_start": 41,
                "source_page_end": 41,
                "raw_text": "未知模板内容,缺少签章信号" * 10,
                "confidence": 0.9,
            },
        ]

    monkeypatch.setattr(
        "app.core.writer.fixed_template.catalog.recognize_templates",
        _all_invalid,
    )

    catalog = await build_fixed_template_catalog_async(chunks, project_id=1)

    assert catalog
    assert all(entry.source == "rule_only" for entry in catalog)


@pytest.mark.asyncio
async def test_catalog_backstops_llm_missed_keys(monkeypatch) -> None:
    """: LLM 部分识别时, 未覆盖的词典 key 由规则兜底增量补入。

 输入含投标函 + 保证金两个模板, LLM 只识别投标函:
 - 投标函保持 source=llm_only 不被兜底覆盖
 - 保证金以 source=rule_only 低置信补入(下游自动 needs_review)
 """
    chunks = _template_chunks()

    async def _ok(*args, **kwargs):
        return [
            {
                "template_key": "bid_letter",
                "title": "投标函",
                "volume_type": "technical",
                "source_page_start": 41,
                "source_page_end": 41,
                "raw_text": (
                    "一、投标函\n致:招标人\n"
                    "本投标人愿意按招标文件要求参加投标并接受全部条款。\n"
                    "投 标 人: (盖单位章)\n签字: 日 期: 年 月 日"
                ),
                "fields": ["投标人"],
                "signature_slots": ["盖单位章", "签字", "日期"],
                "confidence": 0.95,
            },
        ]

    monkeypatch.setattr(
        "app.core.writer.fixed_template.catalog.recognize_templates",
        _ok,
    )

    catalog = await build_fixed_template_catalog_async(chunks, project_id=1)

    by_key = {entry.template_key: entry for entry in catalog}
    assert by_key["bid_letter"].source == "llm_only"
    assert "bid_security_certificate" in by_key
    assert by_key["bid_security_certificate"].source == "rule_only"
    assert by_key["bid_security_certificate"].is_complete_candidate is False


# ---------------------------------------------------------------------------
# LLM 响应归一化
# ---------------------------------------------------------------------------


def test_normalize_response_accepts_top_level_array() -> None:
    items = _normalize_recognize_response([{"template_key": "bid_letter", "title": "投标函"}])
    assert len(items) == 1
    assert items[0]["template_key"] == "bid_letter"


@pytest.mark.parametrize("wrapper_key", ["templates", "items", "data", "entries", "result"])
def test_normalize_response_accepts_wrapper_object(wrapper_key: str) -> None:
    payload = {wrapper_key: [{"template_key": "bid_letter", "title": "投标函"}]}
    items = _normalize_recognize_response(payload)
    assert len(items) == 1
    assert items[0]["template_key"] == "bid_letter"


def test_normalize_response_accepts_bare_single_dict() -> None:
    """LLM 当卷仅有 1 条模板时返回裸 dict, 应被包装为单元素数组。"""
    payload = {
        "template_key": "bid_letter",
        "title": "投标函",
        "raw_text": "致:招标人...",
        "source_page_start": 41,
    }
    items = _normalize_recognize_response(payload)
    assert len(items) == 1
    assert items[0]["template_key"] == "bid_letter"


def test_normalize_response_rejects_irrelevant_dict() -> None:
    """随机 dict 不含 recognize 核心字段时, 不应被误判为单元素模板。"""
    items = _normalize_recognize_response({"foo": "bar", "baz": [1, 2, 3]})
    assert items == []


def test_normalize_response_rejects_non_dict_items() -> None:
    """数组中混杂非 dict 元素时, 仅保留 dict 项。"""
    items = _normalize_recognize_response([{"template_key": "bid_letter"}, "noise", 42, None])
    assert len(items) == 1


def test_normalize_response_handles_none_and_primitive() -> None:
    assert _normalize_recognize_response(None) == []
    assert _normalize_recognize_response("string output") == []
    assert _normalize_recognize_response(42) == []


# ---------------------------------------------------------------------------
# catalog 召回缺口检测 + 针对性兜底
# ---------------------------------------------------------------------------


def _dual_volume_chunks() -> list[SimpleNamespace]:
    """构造技术标 + 商务标双卷的 chunks, 各含一个投标函正文。"""
    return [
        _chunk(38, "第五章 投标文件格式\n封面及目录格式"),
        _chunk(39, "投 标 文 件\n技术标\n投 标 人: (盖单位章)"),
        _chunk(40, "目 录\n一、投标函\n二、投标保证金缴纳证明"),
        _chunk(
            41,
            "一、投标函\n"
            "致:招标人\n"
            "本投标人愿意按招标文件要求参加技术标卷投标,我方承诺信息真实有效。\n"
            "投 标 人: (盖单位章)\n"
            "签字: 日 期: 年 月 日",
        ),
        _chunk(50, "投 标 文 件\n商务标\n投 标 人: (盖单位章)"),
        _chunk(51, "目 录\n一、投标函\n二、开标一览表"),
        _chunk(
            52,
            "一、投标函\n"
            "致:招标人\n"
            "本投标人愿意按招标文件要求参加商务标卷投标,我方承诺信息真实有效。\n"
            "投 标 人: (盖单位章)\n"
            "签字: 日 期: 年 月 日",
        ),
    ]


@pytest.mark.asyncio
async def test_catalog_fills_gap_when_llm_only_recognizes_one_volume(monkeypatch) -> None:
    """ 核心场景: LLM 在双卷输入下只识别 technical bid_letter,
 catalog 应当在 commercial range 内规则兜底补齐一条 commercial bid_letter。
 """
    chunks = _dual_volume_chunks()

    async def _only_technical(*args, **kwargs):
        return [
            {
                "template_key": "bid_letter",
                "title": "投标函",
                "volume_type": "technical",
                "source_page_start": 41,
                "source_page_end": 41,
                "raw_text": (
                    "一、投标函\n致:招标人\n本投标人愿意按招标文件要求参加技术标卷投标。\n"
                    "投 标 人: (盖单位章)\n签字: 日 期: 年 月 日"
                ),
                "signature_slots": ["盖单位章", "签字", "日期"],
                "confidence": 0.95,
            },
        ]

    monkeypatch.setattr(
        "app.core.writer.fixed_template.catalog.recognize_templates",
        _only_technical,
    )

    catalog = await build_fixed_template_catalog_async(chunks, project_id=1389)

    by_vol = {(e.template_key, e.volume_type): e for e in catalog}
    assert ("bid_letter", "technical") in by_vol
    assert by_vol[("bid_letter", "technical")].source == "llm_only"
    assert ("bid_letter", "commercial") in by_vol, (
        "召回缺口检测应当为 commercial 卷补齐 bid_letter"
    )
    assert by_vol[("bid_letter", "commercial")].source == "rule_only"
    assert by_vol[("bid_letter", "commercial")].is_complete_candidate is False


@pytest.mark.asyncio
async def test_catalog_no_gap_fill_when_both_volumes_recognized(monkeypatch) -> None:
    """LLM 已经识别两个 volume 的同 template_key 时, 不应触发额外兜底。"""
    chunks = _dual_volume_chunks()

    async def _both_volumes(*args, **kwargs):
        return [
            {
                "template_key": "bid_letter",
                "title": "投标函",
                "volume_type": "technical",
                "source_page_start": 41,
                "source_page_end": 41,
                "raw_text": (
                    "一、投标函\n致:招标人\n本投标人愿意参加技术标卷投标。\n"
                    "投 标 人: (盖单位章)\n签字: 日 期: 年 月 日"
                ),
                "signature_slots": ["盖单位章"],
                "confidence": 0.95,
            },
            {
                "template_key": "bid_letter",
                "title": "投标函",
                "volume_type": "commercial",
                "source_page_start": 52,
                "source_page_end": 52,
                "raw_text": (
                    "一、投标函\n致:招标人\n本投标人愿意参加商务标卷投标。\n"
                    "投 标 人: (盖单位章)\n签字: 日 期: 年 月 日"
                ),
                "signature_slots": ["盖单位章"],
                "confidence": 0.95,
            },
        ]

    monkeypatch.setattr(
        "app.core.writer.fixed_template.catalog.recognize_templates",
        _both_volumes,
    )

    catalog = await build_fixed_template_catalog_async(chunks, project_id=1389)

    assert len(catalog) == 2
    assert all(entry.source == "llm_only" for entry in catalog), (
        "LLM 正确识别两个 volume 时, 不应当混入 rule_only entry"
    )


@pytest.mark.asyncio
async def test_catalog_gap_fill_only_covers_llm_recognized_keys(monkeypatch) -> None:
    """召回缺口检测只对 LLM 已识别过的 template_key 兜底,
    不引入 LLM 未识别过的全新 template_key, 避免幻觉模板。
    """
    chunks = _dual_volume_chunks()

    async def _only_bid_letter_technical(*args, **kwargs):
        return [
            {
                "template_key": "bid_letter",
                "title": "投标函",
                "volume_type": "technical",
                "source_page_start": 41,
                "source_page_end": 41,
                "raw_text": (
                    "一、投标函\n致:招标人\n本投标人愿意按招标文件要求参加投标。\n"
                    "投 标 人: (盖单位章)\n签字: 日 期: 年 月 日"
                ),
                "signature_slots": ["盖单位章"],
                "confidence": 0.95,
            },
        ]

    monkeypatch.setattr(
        "app.core.writer.fixed_template.catalog.recognize_templates",
        _only_bid_letter_technical,
    )

    catalog = await build_fixed_template_catalog_async(chunks, project_id=1389)

    # 只补 bid_letter 的 commercial 缺口, 不引入其他 LLM 未识别的 template_key
    keys = {entry.template_key for entry in catalog}
    assert keys == {"bid_letter"}, (
        "兜底不应引入 LLM 未识别的 template_key (避免幻觉模板)"
    )


@pytest.mark.asyncio
async def test_catalog_no_gap_fill_for_single_range(monkeypatch) -> None:
    """单 range 项目(无明确分卷)不应触发召回缺口检测。"""
    chunks = [
        _chunk(38, "第五章 投标文件格式"),
        _chunk(39, "投 标 文 件\n技术标\n投 标 人: (盖单位章)"),
        _chunk(
            41,
            "一、投标函\n致:招标人\n本投标人愿意按招标文件要求参加投标。\n"
            "投 标 人: (盖单位章)\n签字: 日 期: 年 月 日",
        ),
    ]

    async def _one_entry(*args, **kwargs):
        return [
            {
                "template_key": "bid_letter",
                "title": "投标函",
                "volume_type": "technical",
                "source_page_start": 41,
                "source_page_end": 41,
                "raw_text": (
                    "一、投标函\n致:招标人\n本投标人愿意按招标文件要求参加投标。\n"
                    "投 标 人: (盖单位章)\n签字: 日 期: 年 月 日"
                ),
                "signature_slots": ["盖单位章"],
                "confidence": 0.95,
            },
        ]

    monkeypatch.setattr(
        "app.core.writer.fixed_template.catalog.recognize_templates",
        _one_entry,
    )

    catalog = await build_fixed_template_catalog_async(chunks, project_id=1)

    assert len(catalog) == 1
    assert catalog[0].source == "llm_only"


@pytest.mark.asyncio
async def test_catalog_gap_fill_skipped_when_target_range_has_no_template(monkeypatch) -> None:
    """缺口 volume range 内根本没有目标 template_key 的可识别内容时,
    不应当强行造一个空 entry 出来。"""
    chunks = [
        _chunk(38, "第五章 投标文件格式\n封面及目录格式"),
        _chunk(39, "投 标 文 件\n技术标\n投 标 人: (盖单位章)"),
        _chunk(
            41,
            "一、投标函\n致:招标人\n本投标人愿意按招标文件要求参加技术标卷投标。\n"
            "投 标 人: (盖单位章)\n签字: 日 期: 年 月 日",
        ),
        _chunk(50, "投 标 文 件\n商务标\n本卷仅包含报价相关材料"),
        # commercial range 没有投标函正文
        _chunk(52, "其他商务材料\n经营财务报告等"),
    ]

    async def _technical_only(*args, **kwargs):
        return [
            {
                "template_key": "bid_letter",
                "title": "投标函",
                "volume_type": "technical",
                "source_page_start": 41,
                "source_page_end": 41,
                "raw_text": (
                    "一、投标函\n致:招标人\n本投标人愿意参加投标。\n"
                    "投 标 人: (盖单位章)\n签字: 日 期: 年 月 日"
                ),
                "signature_slots": ["盖单位章"],
                "confidence": 0.95,
            },
        ]

    monkeypatch.setattr(
        "app.core.writer.fixed_template.catalog.recognize_templates",
        _technical_only,
    )

    catalog = await build_fixed_template_catalog_async(chunks, project_id=1)

    # 应当只有 technical bid_letter, 不强造 commercial bid_letter
    keys_vols = {(e.template_key, e.volume_type) for e in catalog}
    assert ("bid_letter", "technical") in keys_vols
    assert ("bid_letter", "commercial") not in keys_vols, (
        "commercial range 内无可识别投标函时, 不应当强造 entry"
    )
