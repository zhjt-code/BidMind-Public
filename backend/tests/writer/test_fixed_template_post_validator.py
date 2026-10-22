from app.core.writer.fixed_template.post_validator import validate_entries


def _entry(**kwargs):
    base = {
        "template_key": "bid_letter",
        "title": "投标函",
        "source_page_start": 41,
        "source_page_end": 41,
        "raw_text": "一、投标函\n致:招标人\n投标方名称: (盖章)\n投标方法定代表人或授权委托人:(签字)\n日期: 年 月 日",
        "fields": ["投标方名称"],
        "signature_slots": ["盖章", "签字", "日期"],
        "confidence": 0.95,
    }
    base.update(kwargs)
    return base


def test_validate_entries_accepts_valid_template() -> None:
    entries = validate_entries([_entry()], min_page=1, max_page=60)
    assert len(entries) == 1
    assert entries[0].template_key == "bid_letter"
    assert entries[0].source == "llm_only"


def test_validate_entries_rejects_too_short_raw_text() -> None:
    entries = validate_entries([_entry(raw_text="一、投标函")], min_page=1, max_page=60)
    assert entries == []


def test_validate_entries_rejects_page_out_of_range() -> None:
    entries = validate_entries([_entry(source_page_start=99)], min_page=1, max_page=60)
    assert entries == []


def test_validate_entries_accepts_unknown_key_as_custom() -> None:
    """: raw_key 不在词典 + title 无法推断时, 降级为 custom key 而非拒绝。

 custom entry 置信度封顶 0.7 -> is_complete_candidate=False,
 由绑定例外通道放行并自动触发人工复核 gate。
 """
    entries = validate_entries(
        [_entry(template_key="unknown_template", title="拟担任本项目总负责人简历表")],
        min_page=1,
        max_page=60,
    )
    assert len(entries) == 1
    assert entries[0].template_key.startswith("custom:")
    assert entries[0].confidence <= 0.7
    assert entries[0].is_complete_candidate is False


def test_validate_entries_rejects_unknown_key_with_too_short_title() -> None:
    """custom key 要求归一标题 >= 4 字, 过短标题(噪音)仍拒绝。"""
    entries = validate_entries(
        [_entry(template_key="unknown_template", title="表1")],
        min_page=1,
        max_page=60,
    )
    assert entries == []


def test_validate_entries_falls_back_to_title_inference_when_key_unknown() -> None:
    """raw_key 未知但 title 可推断时, 应归一为 title 推断的标准 key。"""
    entries = validate_entries(
        [_entry(template_key="weird_alias_key", title="投标函")],
        min_page=1,
        max_page=60,
    )
    assert len(entries) == 1
    assert entries[0].template_key == "bid_letter"


def test_validate_entries_rejects_directory_like_text() -> None:
    raw_text = "目 录\n一、投标函\n二、开标一览表\n三、开户许可证\n四、投标报价"
    entries = validate_entries([_entry(raw_text=raw_text)], min_page=1, max_page=60)
    assert entries == []


def test_validate_entries_normalizes_technical_suffix_key() -> None:
    """LLM 输出 bid_letter_technical 应归一为 bid_letter, volume_type=technical。"""
    entries = validate_entries(
        [_entry(template_key="bid_letter_technical", volume_type="technical", source_page_start=41)],
        min_page=1,
        max_page=60,
    )
    assert len(entries) == 1
    assert entries[0].template_key == "bid_letter"
    assert entries[0].volume_type == "technical"


def test_validate_entries_normalizes_commercial_suffix_key() -> None:
    entries = validate_entries(
        [_entry(template_key="bid_letter_commercial", volume_type="commercial", source_page_start=52)],
        min_page=1,
        max_page=60,
    )
    assert len(entries) == 1
    assert entries[0].template_key == "bid_letter"
    assert entries[0].volume_type == "commercial"


def test_validate_entries_keeps_both_volume_variants() -> None:
    """同 template_key 但 volume_type 不同, 不应被去重合并。"""
    technical = _entry(
        template_key="bid_letter_technical",
        volume_type="technical",
        source_page_start=41,
    )
    commercial = _entry(
        template_key="bid_letter_commercial",
        volume_type="commercial",
        source_page_start=52,
    )
    entries = validate_entries([technical, commercial], min_page=1, max_page=60)
    assert len(entries) == 2
    volumes = {entry.volume_type for entry in entries}
    assert volumes == {"technical", "commercial"}


def test_validate_entries_cleans_pdf_list_number_fragments() -> None:
    """LLM 输出含 '1\\n、xxx\\n2\\n、yyy' 列表碎片应被合并清洗。"""
    raw_text = (
        "致:招标人\n"
        "1\n"
        "、本投标人提交全部资料。\n"
        "2\n"
        "、本投标人愿意承担责任。\n"
        "投 标 人: (盖单位章)\n"
        "签字: 日期: 年 月 日"
    )
    entries = validate_entries(
        [_entry(raw_text=raw_text, template_key="bid_security_certificate", title="投标保证金缴纳证明")],
        min_page=1,
        max_page=60,
    )
    assert len(entries) == 1
    cleaned = entries[0].raw_text
    assert "1、本投标人提交全部资料。" in cleaned
    assert "2、本投标人愿意承担责任。" in cleaned
    assert "\n1\n" not in cleaned
    assert "\n2\n" not in cleaned


def test_validate_entries_strips_leading_repeat_title() -> None:
    """首行为 '五、法定代表人授权委托书' 与 title 重复时应被剥离。"""
    raw_text = (
        "五、法定代表人授权委托书\n"
        "致:招标人\n"
        "本人系企业法定代表人,特授权代表办理投标。\n"
        "被授权人签名: 职务:\n"
        "投标人(公章)\n"
        "日 期: 年 月 日"
    )
    entries = validate_entries(
        [_entry(
            raw_text=raw_text,
            template_key="legal_representative_authorization",
            title="法定代表人授权委托书",
        )],
        min_page=1,
        max_page=60,
    )
    assert len(entries) == 1
    assert not entries[0].raw_text.lstrip().startswith("五、法定代表人授权委托书")
