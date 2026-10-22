"""精度负例: 封面目录+表单残片+清单+NFKC+容差带+跨章重启不得误报。"""

from __future__ import annotations

import unicodedata

from app.core.document_qa.analyzer import analyze_document
from app.core.document_qa.contracts import (
    DocumentModel,
    QaBlock,
    QaHeading,
    QaPage,
    QaSpan,
    QaTocEntry,
)
from app.core.document_qa.heading_detector import detect_headings
from app.core.document_qa.model_builder import build_paragraphs

_NOISY_RULES = {
    "DOC-STR-002",
    "DOC-TOC-002",
    "DOC-TOC-004",
    "DOC-NUM-002",
    "DOC-CONS-001",
    "DOC-LANG-001",
    "DOC-LANG-003",
    "DOC-LAY-001",
}

_BODY = (
    "我公司将严格按照招标文件及施工组织设计的总体部署组织实施本项目，"
    "确保工程质量达到合格标准并如期交付。"
)


def _span(page: int, text: str, *, font: str = "SimSun", size: float = 12.0, y: float = 0.3) -> QaSpan:
    return QaSpan(
        page=page, text=text, bbox=(0.1, y, 0.9, y + 0.04),
        font=font, size=size, flags=0, color=0,
    )


def _block(page: int, text: str, *, font: str = "SimSun", size: float = 12.0, y: float = 0.3) -> QaBlock:
    return QaBlock(
        page=page, text=text, bbox=(0.1, y, 0.9, y + 0.05),
        spans=(_span(page, text, font=font, size=size, y=y),),
    )


def _page(num: int, text: str, *, images: int = 0) -> QaPage:
    return QaPage(
        page_num=num, width=595, height=842, rotation=0,
        char_count=len(text), image_count=images, table_count=0,
    )


def _fillers(page: int, start_y: float = 0.35) -> list[QaBlock]:
    return [
        _block(page, _BODY, y=start_y + i * 0.05)
        for i in range(6)
    ]


def _precision_model() -> DocumentModel:
    toc_text = (
        "目录\n"
        "一、投标函 .............................................................. 1\n"
        "二、施工组织方案 ........................................................ 3\n"
        "三、投标保证金缴纳证明 .................................................. 5\n"
    )
    visual_p3 = (
        "一、投标函\n"
        "根据贵方投标邀请书，本人正式代表投标方提交文件。本人承诺：1）按招标文件履行义务；"
    )
    nfkc_p3 = unicodedata.normalize("NFKC", visual_p3)
    p5 = (
        "三、投标保证金缴纳证明\n"
        "附：1、人民银行颁发的基本存款账户开户许可证复印件。\n"
        "2、附银行回执单复印件或保证金收据复印件。\n"
        "3、保函扫描件\n"
        f"{_BODY}\n"
    )
    p6 = (
        "投标人名称：某某集团（公章）\n"
        "投标人名称：某某集团日\n"
    )
    p7 = (
        "一、质量承诺\n"
        "（一）人员保障\n"
        f"{_BODY}\n"
    )
    p8 = '1 项目负责人证书-<PERSON_A>\n'
    body_texts = {
        2: toc_text,
        3: nfkc_p3,
        4: "二、施工组织方案\n" + _BODY,
        5: p5,
        6: p6,
        7: p7,
        8: p8,
    }
    blocks = [
        _block(2, "目录", font="SimHei", y=0.12),
        _block(2, "一、投标函 .............................................................. 1", font="SimHei", y=0.18),
        _block(2, "二、施工组织方案 ........................................................ 3", font="SimHei", y=0.22),
        _block(2, "三、投标保证金缴纳证明 .................................................. 5", font="SimHei", y=0.26),
        _block(3, "一、投标函", size=14.0, y=0.12),
        *_fillers(3),
        _block(4, "二、施工组织方案", size=14.0, y=0.12),
        *_fillers(4),
        _block(5, "三、投标保证金缴纳证明", size=14.0, y=0.12),
        _block(5, "1、人民银行颁发的基本存款账户开户许可证复印件。", y=0.20),
        _block(5, "2、附银行回执单复印件或保证金收据复印件。", y=0.24),
        _block(5, "3、保函扫描件", y=0.28),
        *_fillers(5, 0.40),
        *_fillers(6, 0.20),
        _block(6, "66.9 万元", y=0.18),
        _block(7, "一、质量承诺", size=14.0, y=0.12),
        _block(7, "（一）人员保障", y=0.18),
        *_fillers(7, 0.30),
        _block(8, '1 项目负责人证书-<PERSON_A>', size=14.0, y=0.20),
        _block(9, _BODY + "补充一段十一磅正文用于容差带夹具，确认同家族近阈值不报离群。", size=11.0, y=0.30),
        *_fillers(9),
    ]
    # _block 未传 flags, 上面那行 flags= 会 TypeError — 修一下（一）块
    pages = [
        _page(2, toc_text),
        _page(3, visual_p3),
        _page(4, body_texts[4]),
        _page(5, p5),
        _page(6, p6),
        _page(7, p7),
        _page(8, p8, images=2),
        _page(9, _BODY * 3),
    ]
    visual = {
        2: build_paragraphs(toc_text),
        3: build_paragraphs(visual_p3),
        4: build_paragraphs(body_texts[4]),
        5: build_paragraphs(p5),
        6: build_paragraphs(p6),
        7: build_paragraphs(p7),
        8: build_paragraphs(p8),
        9: build_paragraphs(_BODY * 3),
    }
    paragraphs = {k: build_paragraphs(v) for k, v in body_texts.items()}
    paragraphs[3] = build_paragraphs(nfkc_p3)
    paragraphs[9] = visual[9]
    model = DocumentModel(
        document_id=3645, bid_part=None, filename="precision.pdf", page_count=9,
        pages=pages, blocks=blocks, body_texts=body_texts,
        paragraphs=paragraphs, visual_paragraphs=visual, toc_zone={2},
        toc_resolution_reliable=True,
        toc_entries=[
            QaTocEntry(title="投标函", printed_page=1, physical_page=3, order_index=0),
            QaTocEntry(title="施工组织方案", printed_page=3, physical_page=4, order_index=1),
            QaTocEntry(title="投标保证金缴纳证明", printed_page=5, physical_page=5, order_index=2),
            QaTocEntry(title="质量承诺", printed_page=7, physical_page=7, order_index=3),
        ],
    )
    return model


def test_precision_negative_composite_zero_false_positives(qa_rules):
    model = _precision_model()
    model.headings = detect_headings(model, qa_rules)
    displays = {h.display for h in model.headings}
    assert not any("保函扫描件" in d for d in displays)
    assert not any("万元" in (h.title or "") for h in model.headings)
    assert not any(h.page == 2 for h in model.headings)
    analysis = analyze_document(model, qa_rules)
    noisy = [f for f in analysis.findings if f.rule_code in _NOISY_RULES]
    assert noisy == [], [f"{f.rule_code}: {f.message}" for f in noisy]


def test_precision_second_entity_loose_toc_and_paren_restart(qa_rules):
    """同形态第二实体: 两行式目录页 + （一）跨节重启不得报空章节/重号。"""
    model = DocumentModel(
        document_id=2, bid_part=None, filename="e2.pdf", page_count=4,
        pages=[
            _page(1, "目录\n施工方案\n6\n人员配置\n8\n"),
            _page(2, "一、施工方案\n" + _BODY),
            _page(3, "一、人员配置\n（一）简历\n" + _BODY),
            _page(4, "（一）设备\n" + _BODY),
        ],
        body_texts={
            1: "目录\n施工方案\n6\n人员配置\n8\n",
            2: "一、施工方案\n" + _BODY,
            3: "一、人员配置\n（一）简历\n" + _BODY,
            4: "（一）设备\n" + _BODY,
        },
        paragraphs={
            2: [_BODY],
            3: [_BODY],
            4: [_BODY],
        },
        visual_paragraphs={
            2: [_BODY],
            3: [_BODY],
            4: [_BODY],
        },
        toc_zone={1},
        headings=[
            QaHeading(page=2, level=1, number="一", title="施工方案", kind="cn_digit"),
            QaHeading(page=3, level=1, number="一", title="人员配置", kind="cn_digit"),
            QaHeading(page=3, level=2, number="一", title="简历", kind="paren"),
            QaHeading(page=4, level=1, number="二", title="设备配置", kind="cn_digit"),
            QaHeading(page=4, level=2, number="一", title="设备", kind="paren"),
        ],
        blocks=[
            _block(2, "一、施工方案\n" + _BODY),
            *_fillers(2),
            _block(3, "一、人员配置\n" + _BODY),
            *_fillers(3),
            _block(4, "二、设备配置\n" + _BODY),
            *_fillers(4),
        ],
    )
    analysis = analyze_document(model, qa_rules)
    codes = {f.rule_code for f in analysis.findings}
    assert "DOC-STR-002" not in codes
    assert "DOC-NUM-002" not in codes
