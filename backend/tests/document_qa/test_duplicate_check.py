"""重复检查: 长段落精确重复报出; 短文本/高频固定内容不报"""

from app.core.document_qa.checks.duplicate_check import check_duplicate
from tests.document_qa.conftest import make_model

_LONG_PARA = (
    "我公司将严格按照施工组织设计的总体部署组织实施本项目，"
    "确保工程质量达到合格标准，工期满足招标文件要求，"
    "同时建立完善的安全生产管理体系保障现场施工安全。"
)


def test_exact_duplicate_paragraph_reported(qa_rules):
    model = make_model(body_texts={
        2: _LONG_PARA,
        7: _LONG_PARA,
        3: "其他正文内容若干。",
    })
    outcome = check_duplicate(model, qa_rules)
    duplicates = [f for f in outcome.findings if f.rule_code == "DOC-DUP-001"]
    assert len(duplicates) == 1
    assert duplicates[0].evidence.kind == "pair"
    pages = {item.page for item in duplicates[0].evidence.items}
    assert pages == {2, 7}
    assert duplicates[0].title == "段落疑似冗余"  # 疑似冗余而非"错误"
    assert not [f for f in outcome.findings if f.rule_code == "DOC-DUP-004"]


def test_short_repeats_not_reported(qa_rules):
    model = make_model(body_texts={
        1: "详见招标文件。",
        2: "详见招标文件。",
        3: "详见招标文件。",
    })
    outcome = check_duplicate(model, qa_rules)
    assert outcome.findings == []


def test_chrome_like_high_frequency_excluded(qa_rules):
    """出现页占比过高的长内容视为漏网页眉页脚, 不报冗余。"""
    body = {i: _LONG_PARA for i in range(1, 11)}
    model = make_model(body_texts=body)
    outcome = check_duplicate(model, qa_rules)
    assert not [
        f for f in outcome.findings
        if f.rule_code in ("DOC-DUP-001", "DOC-DUP-003")
    ]


def test_frequent_repeats_aggregated_into_single_finding(qa_rules):
    """高频复用段(>=5次, 页占比未达chrome阈值)聚合为一条, 不逐对刷屏。"""
    body: dict[int, str] = {i: f"第{i}页普通正文内容若干。" for i in range(1, 31)}
    for page in (2, 5, 8, 11, 14, 17):
        body[page] = _LONG_PARA  # 6次/30页=20% < 30% chrome 阈值
    model = make_model(body_texts=body)
    outcome = check_duplicate(model, qa_rules)
    aggregated = [f for f in outcome.findings if f.rule_code == "DOC-DUP-003"]
    pairs = [f for f in outcome.findings if f.rule_code == "DOC-DUP-001"]
    assert len(aggregated) == 1
    assert "6" in aggregated[0].message  # 最高重复次数
    assert pairs == []  # 高频组不再产出逐对发现


def test_near_duplicate_one_char_edit(qa_rules):
    tweaked = _LONG_PARA.replace("合格标准", "合格要求")
    model = make_model(body_texts={2: _LONG_PARA, 8: tweaked})
    outcome = check_duplicate(model, qa_rules)
    near = [f for f in outcome.findings if f.rule_code == "DOC-DUP-004"]
    assert len(near) == 1
    pages = {item.page for item in near[0].evidence.items}
    assert pages == {2, 8}
    assert near[0].evidence.kind == "pair"


def test_near_duplicate_digit_edit_second_entity(qa_rules):
    """同形态第二实体: 只改数字的长段落对同样走近重复, 不走精确哈希。"""
    base = (
        "项目实施周期暂按 18 个月安排，分阶段完成资料治理、模型训练与验收交付，"
        "并在每个阶段结束后提交对应成果与进度报告供甲方确认。"
    )
    tweaked = base.replace("18", "16")
    model = make_model(body_texts={4: base, 9: tweaked})
    outcome = check_duplicate(model, qa_rules)
    near = [f for f in outcome.findings if f.rule_code == "DOC-DUP-004"]
    exact = [f for f in outcome.findings if f.rule_code == "DOC-DUP-001"]
    assert len(near) == 1
    assert exact == []
    pages = {item.page for item in near[0].evidence.items}
    assert pages == {4, 9}


def test_table_row_and_heading_repeat_skip_copied_pages(qa_rules):
    """表内重复数据行命中; 另一张表是第二实体。整页复制不再另报标题重复。"""
    from app.core.document_qa.contracts import QaHeading
    from app.core.document_qa.region_text import KIND_TABLE
    from tests.document_qa.conftest import make_line, make_region, with_regions

    def table(page: int, data: str):
        lines = [
            make_line(page, "表头 字段", (0.1, 0.20, 0.8, 0.23)),
            make_line(page, data, (0.1, 0.26, 0.8, 0.29)),
            make_line(page, data, (0.1, 0.32, 0.8, 0.35)),
        ]
        return make_region(page, KIND_TABLE, (0.05, 0.15, 0.9, 0.5), lines)

    copied = "整页复制的正文内容需要足够长。" * 20
    model = with_regions(
        make_model(
            body_texts={
                1: "表格页正文",
                2: "另一张表的正文",
                3: copied,
                4: copied,
                8: "质量保证这一节的正文完全不同。" * 8,
                9: "推广应用这一节写的是别的内容。" * 8,
            },
            headings=[
                QaHeading(page=3, level=2, number="6.2", title="现场实施流程", kind="decimal"),
                QaHeading(page=4, level=2, number="6.2", title="现场实施流程", kind="decimal"),
                QaHeading(page=8, level=2, number="7.1", title="质量保证措施", kind="decimal"),
                QaHeading(page=9, level=2, number="7.1", title="质量保证措施", kind="decimal"),
            ],
        ),
        {
            1: [table(1, "井号 A 备注相同")],
            2: [table(2, "设备 B 状态相同")],
        },
    )
    outcome = check_duplicate(model, qa_rules)
    rows = [item for item in outcome.findings if item.rule_code == "DOC-DUP-005"]
    headings = [item for item in outcome.findings if item.rule_code == "DOC-DUP-006"]
    assert {item.evidence.items[0].page for item in rows} == {1, 2}
    assert len(headings) == 1
    assert "质量保证措施" in headings[0].message
    assert "现场实施流程" not in headings[0].message
    assert rows[0].certainty.value == "possible"
    assert rows[0].closed_measurement is False
    assert rows[0].evidence.items[0].bbox != rows[0].evidence.items[1].bbox
    assert "上一行" in rows[0].evidence.items[1].excerpt


def test_single_cell_repeat_is_not_a_data_row(qa_rules):
    """单格短值和半句不是数据行重复。另一格是第二实体。"""
    from app.core.document_qa.region_text import KIND_TABLE
    from tests.document_qa.conftest import make_line, make_region, with_regions

    def table(page: int, data: str):
        lines = [
            make_line(page, "表头 字段", (0.1, 0.20, 0.8, 0.23)),
            make_line(page, data, (0.1, 0.26, 0.4, 0.29)),
            make_line(page, data, (0.1, 0.32, 0.4, 0.35)),
        ]
        return make_region(page, KIND_TABLE, (0.05, 0.15, 0.9, 0.5), lines)

    model = with_regions(
        make_model(body_texts={22: "甲", 85: "乙"}),
        {22: [table(22, "模板设计")], 85: [table(85, "2023.08")]},
    )
    outcome = check_duplicate(model, qa_rules)
    assert [item for item in outcome.findings if item.rule_code == "DOC-DUP-005"] == []
