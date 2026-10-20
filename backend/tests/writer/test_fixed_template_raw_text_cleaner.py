"""raw_text_cleaner 五条规则 + 边界测试。

覆盖:
- 规则 1: 列表序号碎片合并
- 规则 2/3: 孤立 0 ¥ / 单字符货币 / 无义务孤立纯数字行删除
- 规则 4: 首行重复模板标题剥离
- 规则 5: 短字段值合并
- 边界: 空文本 / 全噪音文本 / 完全干净文本
"""

from app.core.writer.fixed_template.raw_text_cleaner import clean_template_raw_text


def test_merges_list_number_fragments_to_dunhao_form() -> None:
    raw = (
        "致:招标人\n"
        "1\n"
        "、本投标人提交全部资料。\n"
        "2\n"
        "、本投标人愿意承担责任。"
    )
    cleaned = clean_template_raw_text(raw, "投标保证金缴纳证明")
    assert "1、本投标人提交全部资料。" in cleaned
    assert "2、本投标人愿意承担责任。" in cleaned


def test_drops_isolated_currency_and_number_lines() -> None:
    raw = (
        "投 标 人: (盖单位章)\n"
        "0 ¥\n"
        "¥\n"
        "999\n"
        "日 期: 年 月 日"
    )
    cleaned = clean_template_raw_text(raw, "投标函")
    assert "0 ¥" not in cleaned
    assert "999" not in cleaned
    assert "¥" not in cleaned.split("\n")
    assert "投 标 人: (盖单位章)" in cleaned
    assert "日 期: 年 月 日" in cleaned


def test_strips_leading_repeat_title_with_chinese_index() -> None:
    raw = (
        "五、法定代表人授权委托书\n"
        "致:招标人\n"
        "本人系企业法定代表人,特授权代表办理投标。"
    )
    cleaned = clean_template_raw_text(raw, "法定代表人授权委托书")
    assert not cleaned.lstrip().startswith("五、")
    assert "致:招标人" in cleaned


def test_keeps_meaningful_numeric_after_label_line() -> None:
    """孤立短数字若紧跟在以 ':' 结尾的标签行之后, 视为字段值, 不应被删除。"""
    raw = (
        "投 标 人: (盖单位章)\n"
        "成立年份:\n"
        "5\n"
        "日 期: 年 月 日"
    )
    cleaned = clean_template_raw_text(raw, "投标人基本情况表")
    assert "成立年份:" in cleaned
    assert "5" in cleaned


def test_merges_short_followup_for_label_lines() -> None:
    raw = (
        "投 标 人: (盖单位章)\n"
        "致:\n"
        "招标人\n"
        "日 期: 年 月 日"
    )
    cleaned = clean_template_raw_text(raw, "投标函")
    assert "致: 招标人" in cleaned
    assert "致:\n招标人" not in cleaned


def test_returns_original_when_input_empty() -> None:
    assert clean_template_raw_text("", "投标函") == ""


def test_falls_back_to_original_when_cleaning_empties_everything() -> None:
    """若清洗后内容为空, 回退原文以避免数据丢失。"""
    raw = "0 ¥\n¥\n9"
    assert clean_template_raw_text(raw, "投标函") == raw


def test_strips_leading_title_with_subset_relation() -> None:
    """节点 title 与首行标题互为子串且 >= 4 字时, 剥离。"""
    raw = (
        "三、投标保证金缴纳证明\n"
        "致:招标人\n"
        "本投标人自愿参加本项目投标。\n"
        "投 标 人: (盖单位章)\n"
        "日 期: 年 月 日"
    )
    cleaned = clean_template_raw_text(raw, "投标保证金缴纳证明复印件(盖单位章)")
    assert not cleaned.lstrip().startswith("三、")
    assert "致:招标人" in cleaned


def test_strips_leading_title_via_alias_match() -> None:
    """首行命中模板别名 (投标书 是 bid_letter 的别名), 剥离。"""
    raw = (
        "一、投标书\n"
        "致:招标人\n"
        "投 标 人: (盖单位章)\n"
        "日 期: 年 月 日"
    )
    cleaned = clean_template_raw_text(raw, "投标函")
    assert not cleaned.lstrip().startswith("一、")
    assert "致:招标人" in cleaned


def test_idempotent_on_clean_text() -> None:
    raw = (
        "致:<ORG_ENERGY_1>\n"
        "投 标 人: (盖单位章)\n"
        "法定代表人或其委托代理人: (签字)\n"
        "日 期: 年 月 日"
    )
    once = clean_template_raw_text(raw, "投标函")
    twice = clean_template_raw_text(once, "投标函")
    assert once == twice
