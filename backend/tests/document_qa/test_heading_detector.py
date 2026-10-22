"""标题识别: 弱句式必须强信号; 行内混排标题(金标形态)可采集; 降级路径不启用"""

from app.core.document_qa.checks.numbering_check import check_numbering
from app.core.document_qa.checks.structure_check import check_structure
from app.core.document_qa.contracts import DocumentModel, QaBlock, QaSpan
from app.core.document_qa.heading_detector import detect_headings

_BODY_SIZE = 10.5
_BOLD_FLAG = 1 << 4


def _block(page: int, text: str, *, size: float = _BODY_SIZE, flags: int = 0) -> QaBlock:
    return QaBlock(
        page=page,
        text=text,
        bbox=(0.1, 0.1, 0.9, 0.15),
        spans=(QaSpan(
            page=page, text=text, bbox=(0.1, 0.1, 0.9, 0.15),
            font="SimSun", size=size, flags=flags, color=0,
        ),),
    )


def _model_with_blocks(blocks: list[QaBlock]) -> DocumentModel:
    # 21 个正文 block 保证 body 基准字号可计算(启用信号判定路径)
    fillers = [
        _block(1, f"这是用于建立正文字号基准的填充句子第{i}条，内容无实际意义。")
        for i in range(21)
    ]
    return DocumentModel(
        document_id=1, bid_part=None, filename="t.pdf", page_count=3,
        blocks=fillers + blocks,
    )


def test_top_space_heading_needs_font_signal():
    """"1␣标题"大字号识别为一级标题; 附件标题(第二实体)同门槛。"""
    model = _model_with_blocks([
        _block(2, "1 项目理解与总体目标", size=16.0),
        _block(3, "附件2 数据字段说明", size=15.0),
    ])
    headings = detect_headings(model)
    numbers = {h.number for h in headings}
    assert "1" in numbers
    assert "附件2" in numbers
    assert all(h.level == 1 for h in headings)


def test_top_space_without_signal_rejected():
    """表格序号列"1 资料治理"(正文字号、非加粗)不得识别为标题。"""
    model = _model_with_blocks([
        _block(2, "1 资料治理"),
        _block(2, "附件2 数据字段说明"),
    ])
    assert detect_headings(model) == []


def test_bold_signal_accepted():
    model = _model_with_blocks([
        _block(2, "3 研究内容与技术路线", flags=_BOLD_FLAG),
    ])
    headings = detect_headings(model)
    assert [h.number for h in headings] == ["3"]


def test_degraded_text_path_skips_weak_kinds():
    """无 span 信号的降级路径: 弱句式不启用, 显式分隔符句式保持识别。"""
    model = DocumentModel(
        document_id=1, bid_part=None, filename="t.pdf", page_count=1,
        body_texts={1: "1 项目理解与总体目标\n3.1 施工方案"},
    )
    headings = detect_headings(model)
    assert [h.number for h in headings] == ["3.1"]


def _inline_block(
    page: int,
    heading: str,
    body: str,
    *,
    heading_size: float = 14.0,
    body_size: float = _BODY_SIZE,
    flags: int = _BOLD_FLAG,
) -> QaBlock:
    return QaBlock(
        page=page,
        text=heading + body,
        bbox=(0.1, 0.2, 0.9, 0.35),
        spans=(
            QaSpan(
                page=page, text=heading, bbox=(0.1, 0.2, 0.55, 0.24),
                font="SimHei", size=heading_size, flags=flags, color=0,
            ),
            QaSpan(
                page=page, text=body, bbox=(0.55, 0.2, 0.9, 0.28),
                font="SimSun", size=body_size, flags=0, color=0,
            ),
        ),
    )


def test_inline_mixed_heading_collected():
    """金标形态: 「6.2 标题」与正文同段, 首 span 强信号采集为标题。"""
    body = "数据处理任务统一由甲方提供接口，乙方负责接入与校核工作。"
    model = _model_with_blocks([
        _inline_block(36, "6.2 生产动态分析", body),
    ])
    headings = detect_headings(model)
    assert [(h.number, h.title, h.page) for h in headings] == [
        ("6.2", "生产动态分析", 36),
    ]
    assert headings[0].bbox is not None
    assert headings[0].bbox[2] <= 0.55 + 1e-9  # 用标题 span 框, 不是整段


def test_inline_heading_duplicate_feeds_numbering(qa_rules):
    """两处行内 6.2 进入编号差分, 报重号。"""
    body_a = "数据处理任务统一由甲方提供接口，乙方负责接入与校核工作。"
    body_b = "另一处同样编号的生产动态分析正文用于夹具验证重号判定。"
    model = _model_with_blocks([
        _inline_block(36, "6.2 生产动态分析", body_a),
        _inline_block(38, "6.2 生产动态分析", body_b),
    ])
    model.headings = detect_headings(model)
    outcome = check_numbering(model, qa_rules)
    duplicates = [f for f in outcome.findings if f.rule_code == "DOC-NUM-002"]
    assert len(duplicates) == 1
    assert "6.2" in duplicates[0].message


def test_inline_heading_second_entity_enters_tree(qa_rules):
    """同形态第二实体: 行内 3.1.1 进入标题树; 与顶级「1 」异族, 不报层级跳跃。"""
    model = _model_with_blocks([
        _block(1, "1 项目理解与总体目标", size=16.0),
        _inline_block(2, "3.1.1 现场准备", "本节说明施工现场准备的具体安排以及人员进场条件。"),
    ])
    model.headings = detect_headings(model)
    assert any(h.number == "3.1.1" for h in model.headings)
    outcome = check_structure(model, qa_rules)
    assert not any(f.rule_code == "DOC-STR-001" for f in outcome.findings)


def test_same_span_decimal_leftover_collected():
    """金标形态: 同一正文字号 span 内「6.2 标题  正文…」取前缀为标题。"""
    body = (
        "本节标题在前文已经出现，此处因模板复制被重复保留。"
        "推广阶段仍沿用试验申请与效果跟踪口径。"
    )
    model = _model_with_blocks([
        _block(38, "6.2 现场实施流程  " + body),
    ])
    headings = detect_headings(model)
    assert [(h.number, h.title, h.page) for h in headings] == [
        ("6.2", "现场实施流程", 38),
    ]
    assert headings[0].bbox is not None
    assert headings[0].bbox[2] < 0.9


def test_same_span_duplicate_feeds_numbering(qa_rules):
    """P36 独立 6.2 + P38 同 span 混排 6.2 -> 章节重号(金标 #5)。"""
    body = (
        "本节标题在前文已经出现，此处因模板复制被重复保留。"
        "推广阶段仍沿用试验申请与效果跟踪口径。"
    )
    model = _model_with_blocks([
        _block(36, "6.2 现场实施流程", size=14.0),
        _block(38, "6.2 现场实施流程  " + body),
    ])
    model.headings = detect_headings(model)
    assert [h.number for h in model.headings if h.number == "6.2"] == ["6.2", "6.2"]
    outcome = check_numbering(model, qa_rules)
    duplicates = [f for f in outcome.findings if f.rule_code == "DOC-NUM-002"]
    assert any("6.2" in f.message for f in duplicates)


def test_same_span_second_entity_enters_tree(qa_rules):
    """同形态第二实体: 同 span「3.1.1 标题 正文」进入标题树; 异族不报跳跃。"""
    body = "本节说明施工现场准备的具体安排以及人员进场条件与机具配置。"
    model = _model_with_blocks([
        _block(1, "1 项目理解与总体目标", size=16.0),
        _block(2, "3.1.1 现场准备 " + body),
    ])
    model.headings = detect_headings(model)
    assert any(h.number == "3.1.1" for h in model.headings)
    outcome = check_structure(model, qa_rules)
    assert not any(f.rule_code == "DOC-STR-001" for f in outcome.findings)


def test_dotted_toc_line_rejected(qa_rules):
    model = _model_with_blocks([
        _block(2, "一、投标函 .............................................................. 1"),
    ])
    model.toc_zone = {2}
    assert detect_headings(model, qa_rules) == []


def test_unit_title_and_list_sibling_rejected(qa_rules):
    model = _model_with_blocks([
        _block(5, "1、人民银行颁发的基本存款账户开户许可证复印件。"),
        _block(5, "2、附银行回执单复印件或保证金收据复印件。"),
        _block(5, "3、保函扫描件"),
        _block(6, "66.9 万元", size=12.0),
    ])
    headings = detect_headings(model, qa_rules)
    assert not any("保函" in (h.title or "") for h in headings)
    assert not any(h.title == "万元" for h in headings)


def test_top_digit_body_size_rejected(qa_rules):
    """正文字号的「1.证书标签」不得靠短行豁免入树。"""
    model = _model_with_blocks([
        _block(11, '1.项目负责人证书-<PERSON_A>'),
        _block(16, '2.技术专家证书-<PERSON_B>'),
    ])
    headings = detect_headings(model, qa_rules)
    assert not any(h.kind == "top_digit" for h in headings)


def test_arabic_list_under_section_rejected(qa_rules):
    """同形态第二实体: 节标题下的 1、/2、 清单即使略大字号也不升为一级标题。"""
    model = _model_with_blocks([
        _block(39, "8.3 施工业绩", size=14.0),
        _block(39, "1、近三年施工业绩", size=14.0),
        _block(39, "2、近三年业绩合同证明", size=14.0),
    ])
    headings = detect_headings(model, qa_rules)
    assert [h.number for h in headings] == ["8.3"]


def test_top_space_leftover_without_signal_rejected():
    """弱句式即使后续有长正文, 无字号/字重仍不采集(表格序号列+说明)。"""
    model = _model_with_blocks([
        _block(2, "1 资料治理 这是一段足够长的单元格说明文字用来把余量撑过二十字以上。"),
    ])
    assert detect_headings(model) == []
