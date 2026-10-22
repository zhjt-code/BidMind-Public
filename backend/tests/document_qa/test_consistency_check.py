"""实体一致性: 金额冲突 + 工期冲突(同机制两实体) + 归一化后同值不报"""

from app.core.document_qa.checks.consistency_check import check_consistency
from tests.document_qa.conftest import make_model


def test_money_conflict_reported(qa_rules):
    model = make_model(body_texts={
        3: "投标总价：人民币12,580,000元。",
        120: "投标报价：12,850,000元。",
    })
    outcome = check_consistency(model, qa_rules)
    conflicts = [f for f in outcome.findings if "投标报价" in f.title]
    assert len(conflicts) == 1
    assert conflicts[0].severity.value == "P0"
    assert conflicts[0].evidence.kind == "pair"
    pages = {item.page for item in conflicts[0].evidence.items}
    assert pages == {3, 120}


def test_duration_conflict_second_entity(qa_rules):
    model = make_model(body_texts={
        12: "总工期：180日历天。",
        231: "工期：150天。",
    })
    outcome = check_consistency(model, qa_rules)
    conflicts = [f for f in outcome.findings if "工期" in f.title]
    assert len(conflicts) == 1
    assert conflicts[0].severity.value == "P1"


def test_normalized_equal_values_not_reported(qa_rules):
    """1258万元 与 12,580,000元 归一化相等; 180天 与 180日历天 归一化相等。"""
    model = make_model(body_texts={
        3: "投标总价：1258万元。工期：180日历天。",
        50: "投标报价：12,580,000元。总工期：180天。",
    })
    outcome = check_consistency(model, qa_rules)
    assert outcome.findings == []


# ── 金标3638 锚定句式修复(形态: 同义触发词/空格分隔/计划口径变体) ──────────


def test_duration_synonym_and_planned_variant_conflict(qa_rules):
    """科研口径"研究周期为18个月"与计划口径"暂按16个月"同槽位冲突。"""
    model = make_model(body_texts={
        2: "我单位认真研究了招标文件，计划研究周期为18个月。",
        36: "为压缩海上作业窗口，专题计划表中暂按16个月完成全部现场工作。",
    })
    outcome = check_consistency(model, qa_rules)
    conflicts = [f for f in outcome.findings if "周期" in f.title or "工期" in f.title]
    assert len(conflicts) == 1
    assert "18个月" in (conflicts[0].evidence.expected or "") + (conflicts[0].evidence.actual or "")


def test_budget_amount_conflict_second_entity(qa_rules):
    """预算总额 vs 经费测算汇总金额(同槽位不同触发词变体)。"""
    model = make_model(body_texts={
        49: "项目预算总额为286.0万元。\n经费测算表汇总金额为268.0万元，据实结算。",
    })
    outcome = check_consistency(model, qa_rules)
    conflicts = [f for f in outcome.findings if "预算" in f.title]
    assert len(conflicts) == 1
    assert conflicts[0].severity.value == "P0"


def test_project_no_space_separated_conflict(qa_rules):
    """"编号␣值"空格句式与冒号句式同槽位比对(017 vs 071)。"""
    model = make_model(body_texts={
        1: "项目编号：KY-2026-OF-017。",
        22: "数据处理任务统一登记项目编号 KY-2026-OF-071，按批次归档。",
    })
    outcome = check_consistency(model, qa_rules)
    conflicts = [f for f in outcome.findings if "项目编号" in f.title]
    assert len(conflicts) == 1
    pages = {item.page for item in conflicts[0].evidence.items}
    assert pages == {1, 22}


def test_org_name_seal_and_date_debris_not_conflict(qa_rules):
    """同一组织名带印章/日期残片不应报冲突。"""
    model = make_model(body_texts={
        6: "投标人名称：<ORG_7>（公章）",
        7: "投标人名称：<ORG_7>日",
    })
    outcome = check_consistency(model, qa_rules)
    assert not [f for f in outcome.findings if "投标人" in f.title]


def test_org_name_group_suffix_second_entity(qa_rules):
    """同形态第二实体: 集团 vs 集团公司 归一化后不报。"""
    model = make_model(body_texts={
        2: "投标人名称：某某集团",
        8: "投标人名称：某某集团公司",
    })
    outcome = check_consistency(model, qa_rules)
    assert not [f for f in outcome.findings if "投标人" in f.title]


def test_milestone_acceptance_date_conflict(qa_rules):
    """同一阶段验收里程碑两处日期不一致(date 槽位)。"""
    model = make_model(body_texts={
        43: (
            "项目第一阶段验收计划于2027年12月31日前完成，提交数据库初版。\n"
            "进度横道表中同一“第一阶段验收”节点标注为2028年3月31日，应统一计划基准。"
        ),
    })
    outcome = check_consistency(model, qa_rules)
    conflicts = [f for f in outcome.findings if "验收" in f.title]
    assert len(conflicts) == 1


def test_title_variant_and_same_unit_quantity_not_conflict(qa_rules):
    """正文里的删字词干不报标题不一致。同段同单位不同数值不报; 另一单位对是第二实体。"""
    from app.core.document_qa.contracts import QaHeading

    model = make_model(
        body_texts={
            11: "正文一处写为海上低渗油藏智能注采优化技术研究, 缺少中间二字。",
            31: "注水强度控制上限按单井60 m3/d设置，月度统筹表中另以1800 m³/月进行约束。",
            32: "同一口井分别记录10 m3/d与20 m3/d, 单位相同只是数值不同。",
            33: "循环流量按12 L/h设计, 另一栏写成12 L/min。",
        },
        headings=[
            QaHeading(
                page=1, level=1, number=None,
                title="海上低渗油藏智能注采优化关键技术研究", kind="decimal",
            ),
        ],
    )
    outcome = check_consistency(model, qa_rules)
    variants = [item for item in outcome.findings if item.anchor.startswith("title_variant:")]
    rates = [item for item in outcome.findings if "数量口径" in item.title]
    assert variants == []
    pages = {item.page for item in rates[0].evidence.items} if rates else set()
    assert any(item.evidence.items[0].page == 31 for item in rates)
    assert all(item.evidence.items[0].page != 32 for item in rates)
    assert any(item.evidence.items[0].page == 33 for item in rates)
    assert 31 in pages or any(
        {piece.page for piece in item.evidence.items} == {31} for item in rates
    )
