from types import SimpleNamespace

from app.core.writer.material_matcher import MaterialMatcher
from app.core.writer.numbering import (
    compute_chapter_numbers,
    format_number_for_depth,
    strip_existing_number,
    strip_existing_number_strict,
)
from app.core.writer.section_composer import SectionComposer
from app.core.writer.section_structure import (
    detect_numbering_depth,
    extract_text_without_number,
    normalize_section_html,
    parse_section_blocks,
)


def test_format_number_for_depth_uses_five_level_rules() -> None:
    assert format_number_for_depth(1, 0) == "一、"
    assert format_number_for_depth(2, 0) == "（一）"
    assert format_number_for_depth(3, 0) == "1、"
    assert format_number_for_depth(4, 0) == "（1）"
    assert format_number_for_depth(5, 0) == "①"
    assert format_number_for_depth(6, 0) == "1.1、"


def test_compute_chapter_numbers_resets_by_parent() -> None:
    nodes = [
        SimpleNamespace(id=1, parent_id=None, depth=0, sort_order=0),
        SimpleNamespace(id=2, parent_id=1, depth=1, sort_order=0),
        SimpleNamespace(id=3, parent_id=2, depth=2, sort_order=0),
        SimpleNamespace(id=4, parent_id=2, depth=2, sort_order=1),
        SimpleNamespace(id=5, parent_id=1, depth=1, sort_order=1),
        SimpleNamespace(id=6, parent_id=5, depth=2, sort_order=0),
    ]

    result = compute_chapter_numbers(nodes)

    assert result[2] == "一、"
    assert result[3] == "（一）"
    assert result[4] == "（二）"
    assert result[5] == "二、"
    assert result[6] == "（一）"


def test_strip_existing_number_supports_fullwidth_and_circled_prefix() -> None:
    assert strip_existing_number("（一）项目概况") == "项目概况"
    assert strip_existing_number("1、服务目标") == "服务目标"
    assert strip_existing_number("1 投标函") == "投标函"
    assert strip_existing_number("2\t法定代表人身份证明") == "法定代表人身份证明"
    assert strip_existing_number("（1）实施步骤") == "实施步骤"
    assert strip_existing_number("①响应原则") == "响应原则"


def test_strip_existing_number_supports_chapter_and_part_prefixes() -> None:
    assert strip_existing_number("第一章 总则") == "总则"
    assert strip_existing_number("第一节 项目概况") == "项目概况"
    assert strip_existing_number("第一部分 投标函") == "投标函"
    assert strip_existing_number("第二部分 技术方案") == "技术方案"
    assert strip_existing_number("第1部分 技术标") == "技术标"


def test_strip_existing_number_strict_keeps_title_editing_and_cleans_pure_markers() -> None:
    assert strip_existing_number_strict("（三）") == ""
    assert strip_existing_number_strict("一、") == ""
    assert strip_existing_number_strict("①") == ""
    assert strip_existing_number_strict("（三）（三）项目概况") == "项目概况"


def test_detect_numbering_depth_prefers_sixth_level_before_decimal() -> None:
    assert detect_numbering_depth("1.1、子项") == 6
    assert detect_numbering_depth("1、一级项") == 3


def test_extract_text_without_number_removes_space_prefixed_digits() -> None:
    assert extract_text_without_number("2 法定代表人身份证明") == "法定代表人身份证明"
    assert extract_text_without_number("3") == ""
    assert extract_text_without_number("0 ¥") == ""
    assert extract_text_without_number("（一）.") == ""


def test_parse_section_blocks_skips_pure_digit_noise() -> None:
    blocks = parse_section_blocks("3\n<p>正文内容</p>")

    assert len(blocks) == 1
    assert blocks[0].kind == "paragraph"
    assert blocks[0].text == "正文内容"


def test_parse_section_blocks_preserves_pure_heading_marker_without_text_duplication() -> None:
    blocks = parse_section_blocks("<h4>（三）</h4><p>正文内容</p>")

    assert len(blocks) == 2
    assert blocks[0].kind == "heading"
    assert blocks[0].pure_numbering is True
    assert blocks[0].text == ""
    assert blocks[1].kind == "paragraph"
    assert blocks[1].text == "正文内容"


def test_parse_section_blocks_marks_visible_numbering_as_hint() -> None:
    blocks = parse_section_blocks("<h4>（1）核心资质与专业认证</h4>")

    assert len(blocks) == 1
    assert blocks[0].kind == "heading"
    assert blocks[0].explicit_depth == 4
    assert blocks[0].prefer_structure_depth is True
    assert blocks[0].text == "核心资质与专业认证"


def test_parse_section_blocks_merges_number_stub_with_next_line() -> None:
    blocks = parse_section_blocks("1.\n招标文件要求的其它内容。")

    assert len(blocks) == 1
    assert blocks[0].kind == "numbered_paragraph"
    assert blocks[0].text == "招标文件要求的其它内容。"


def test_normalize_fixed_template_text_repairs_template_noise() -> None:
    raw = """
    三、投标保证金缴纳证明
    金额为人民币（大写） 元（零元整）。
    0 ¥
    附： 、人民银行颁发的基本存款账户开户许可证复印件。
    1
    、附银行回执单复印件或保证金收据复印件。
    2
    、保函扫描件
    3
    注：采用转账方式递交投标保证金的，仅需提供第 、 项资料。采用
    1 2
    保函递交投标保证金的，仅需提供第 项资料，并在开标现场递交保函原件。
    3
    """

    normalized = SectionComposer._normalize_fixed_template_text(raw)

    assert "0 ¥" not in normalized
    assert "附：1、人民银行颁发的基本存款账户开户许可证复印件。" in normalized
    assert "2、附银行回执单复印件或保证金收据复印件。" in normalized
    assert "3、保函扫描件" in normalized
    assert "第1、2项资料" in normalized
    assert "第3项资料" in normalized


def test_normalize_fixed_template_text_repairs_broken_parenthesized_items() -> None:
    raw = """
    2.
    本人代表投标方保证，所提供的全部资料是真实的。本人承诺：
    ） 我方将按“招标文件”的规定履行招标过程中投标方的责任和义务；
    1 .
    ） 本投标自开标日起有效期为 个日历日；
    3 . 60
    """

    normalized = SectionComposer._normalize_fixed_template_text(raw)

    assert "2. 本人代表投标方保证，所提供的全部资料是真实的。本人承诺：" in normalized
    assert "1、我方将按“招标文件”的规定履行招标过程中投标方的责任和义务；" in normalized
    assert "3、本投标自开标日起有效期为60个日历日；" in normalized


def test_normalize_section_html_drops_plain_text_duplicate_title_line() -> None:
    normalized = normalize_section_html(
        "（一）投标函\n（一）法定代表人身份证明及授权委托书",
        "投标函",
    )

    assert "（一）投标函" not in normalized
    assert "（一）法定代表人身份证明及授权委托书" in normalized


def test_normalize_section_html_strips_markdown_html_fence() -> None:
    normalized = normalize_section_html(
        "```html\n<p>响应服务方案。</p>\n```",
        "服务方案",
    )

    assert normalized == "<p>响应服务方案。</p>"
    assert "```" not in normalized


def test_rebuild_bid_opening_table_html_normalizes_tabular_template() -> None:
    raw = """
    （招标人名称）：<ORG_ENERGY_1>在研究了招标文件中所有文件和合同条件、技术资料后，我们对宁东油田防腐药剂及防腐工具性能测试项目投标报价如下：
    项目名称 报价 备注在本项目最高限价基础上整体下浮（不含税）
    项目 %
    注： 、投标单位如果需要对报价或其它内容加以说明，可在备注栏填写；
    （一）此表应经法定代表人或授权代表人签名，并盖上公章；
    """

    html = SectionComposer._rebuild_tabular_fixed_template("开标一览表", raw)

    assert "<table>" in html
    assert "<th>项目名称</th><th>报价</th><th>备注</th>" in html
    assert "项目 %" not in html
    assert "注： 、" not in html
    assert "注：投标单位如果需要对报价或其它内容加以说明，可在备注栏填写；" in html
    assert "（一）此表应经法定代表人或授权代表人签名，并盖上公章；" in html


def test_fuzzy_title_match_accepts_fixed_template_title_variants() -> None:
    assert MaterialMatcher._fuzzy_title_match("投标函及附录", "投标函附录")
    assert MaterialMatcher._fuzzy_title_match("开标一览表格式", "开标一览表")


def test_compute_chapter_numbers_covers_deep_directory_tree() -> None:
    """验证编号架构对 4 级深层目录树的完整覆盖(原型验证转正式测试)"""
    _id = 0

    def n(title, depth, sort_order, parent_id=None):
        nonlocal _id
        _id += 1
        return SimpleNamespace(
            id=_id, parent_id=parent_id,
            depth=depth, sort_order=sort_order, title=title,
        )

    nodes = []

    vol1 = n("资格及资信证明部分", 0, 0)
    nodes.append(vol1)

    n_letter = n("投标函", 1, 0, vol1.id)
    nodes.append(n_letter)

    n_parts = n("投标文件组成部分", 2, 0, n_letter.id)
    nodes.append(n_parts)
    for i, t in enumerate(["资格及资信证明部分", "报价部分", "技术商务部分"]):
        nodes.append(n(t, 3, i, n_parts.id))

    n_confirm = n("确认", 2, 1, n_letter.id)
    nodes.append(n_confirm)
    for i in range(2):
        nodes.append(n(f"确认条款{i+1}", 3, i, n_confirm.id))

    n_promise = n("承诺及声明", 2, 2, n_letter.id)
    nodes.append(n_promise)
    for i in range(10):
        nodes.append(n(f"承诺条款{i+1}", 3, i, n_promise.id))

    n_qual = n("投标人的资格及资信证明文件", 1, 1, vol1.id)
    nodes.append(n_qual)
    n_auth = n("单位授权书", 2, 0, n_qual.id)
    nodes.append(n_auth)
    n_proof = n("证明材料", 2, 1, n_qual.id)
    nodes.append(n_proof)
    nodes.append(n("资格承诺函", 3, 0, n_proof.id))
    nodes.append(n("资格证明材料", 3, 1, n_proof.id))

    nodes.append(n("投标保证金", 1, 2, vol1.id))

    vol2 = n("报价部分", 0, 1)
    nodes.append(vol2)
    for i, t in enumerate(["开标一览表", "报价明细表"]):
        nodes.append(n(t, 1, i, vol2.id))

    result = compute_chapter_numbers(nodes)
    by_title = {nd.title: nd.id for nd in nodes}

    def nr(title):
        return result.get(by_title.get(title, -1), "???")

    assert nr("投标函") == "一、"
    assert nr("投标人的资格及资信证明文件") == "二、"
    assert nr("投标保证金") == "三、"
    assert nr("投标文件组成部分") == "（一）"
    assert nr("确认") == "（二）"
    assert nr("承诺及声明") == "（三）"
    assert nr("承诺条款1") == "1、"
    assert nr("承诺条款10") == "10、"
    assert nr("单位授权书") == "（一）"
    assert nr("证明材料") == "（二）"
    assert nr("资格承诺函") == "1、"
    assert nr("资格证明材料") == "2、"
    assert nr("开标一览表") == "一、"
    assert nr("报价明细表") == "二、"
