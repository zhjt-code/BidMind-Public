"""证据几何: 页内块定位 + 已有 bbox 不覆盖 + 页级全页框(第二实体: 金额槽 vs 工期槽)"""

from app.core.document_qa.analyzer import analyze_document
from app.core.document_qa.checks.consistency_check import collect_slot_ledger
from app.core.document_qa.checks.hygiene_check import check_hygiene
from app.core.document_qa.checks.page_integrity_check import check_page_integrity
from app.core.document_qa.contracts import (
    Evidence,
    EvidenceItem,
    Finding,
    QaBlock,
    QaCategory,
    QaCertainty,
    QaSeverity,
    QaSpan,
)
from app.core.document_qa.envelope_analyzer import EnvelopeDocFeatures, analyze_envelope
from app.core.document_qa.evidence_geom import (
    PAGE_BBOX,
    enrich_findings,
    locate_bbox,
)
from tests.document_qa.conftest import make_model
from tests.document_qa.test_page_integrity_check import _page


def _block(page: int, text: str, bbox, *, spans=()):
    return QaBlock(page=page, text=text, bbox=bbox, spans=tuple(spans))


def test_locate_bbox_prefers_matching_block():
    model = make_model(body_texts={
        3: "投标总价：人民币12,580,000元。",
        12: "总工期：180日历天。",
    })
    money_box = (0.12, 0.40, 0.88, 0.46)
    duration_box = (0.10, 0.55, 0.70, 0.61)
    model.blocks = [
        _block(3, "投标总价：人民币12,580,000元。", money_box),
        _block(12, "总工期：180日历天。", duration_box),
    ]
    assert locate_bbox(model, 3, "12,580,000元") == money_box
    assert locate_bbox(model, 12, "180日历天") == duration_box


def test_enrich_fills_missing_preserves_existing():
    model = make_model(body_texts={2: "项目经理：XXXX。"})
    kept = (0.2, 0.2, 0.4, 0.25)
    model.blocks = [_block(2, "项目经理：XXXX。", (0.1, 0.3, 0.9, 0.38))]
    finding = Finding(
        rule_code="DOC-HYG-001",
        category=QaCategory.HYGIENE,
        severity=QaSeverity.P2,
        certainty=QaCertainty.PROBABLE,
        title="t",
        message="m",
        evidence=Evidence(kind="single", items=(
            EvidenceItem(excerpt="XXXX", page=2),
            EvidenceItem(excerpt="XXXX", page=2, bbox=kept),
        )),
    )
    findings = [finding]
    enrich_findings(model, findings)
    assert findings[0].evidence.items[0].bbox == (0.1, 0.3, 0.9, 0.38)
    assert findings[0].evidence.items[1].bbox == kept


def test_hygiene_placeholder_bbox_after_analyze(qa_rules):
    """夹具一: 占位符摘录能回到块框。"""
    model = make_model(body_texts={2: "项目经理：XXXX，联系电话待补充。"})
    box = (0.15, 0.22, 0.80, 0.30)
    model.blocks = [_block(2, "项目经理：XXXX，联系电话待补充。", box)]
    analysis = analyze_document(model, qa_rules)
    placeholders = [f for f in analysis.findings if f.rule_code == "DOC-HYG-001"]
    assert placeholders
    assert all(
        item.bbox == box
        for finding in placeholders
        for item in finding.evidence.items
    )


def test_slot_ledger_bbox_copied_to_envelope(qa_rules):
    """夹具二: 工期槽位 bbox 经台账进入跨卷证据(与金额夹具同机制)。"""
    duration_box = (0.11, 0.50, 0.66, 0.56)
    model = make_model(
        body_texts={12: "总工期：180日历天。"},
        document_id=1,
        bid_part="commercial",
    )
    model.blocks = [_block(12, "总工期：180日历天。", duration_box)]
    ledger = collect_slot_ledger(model, qa_rules)
    occs = (ledger.get("duration") or {}).get("occurrences") or []
    assert occs
    assert occs[0].get("bbox") == list(duration_box)

    other_box = (0.20, 0.33, 0.71, 0.40)
    rows = analyze_envelope([
        EnvelopeDocFeatures(1, "commercial", {
            "entity_slots": {
                "duration": {
                    "label": "工期",
                    "severity": "P1",
                    "occurrences": [{
                        "value": "180天", "normalized": "180",
                        "page": 12, "excerpt": "总工期：180日历天。",
                        "bbox": list(duration_box),
                    }],
                },
            },
        }),
        EnvelopeDocFeatures(2, "technical", {
            "entity_slots": {
                "duration": {
                    "label": "工期",
                    "severity": "P1",
                    "occurrences": [{
                        "value": "150天", "normalized": "150",
                        "page": 20, "excerpt": "工期：150天。",
                        "bbox": list(other_box),
                    }],
                },
            },
        }),
    ])
    conflicts = [r for r in rows if r["rule_code"] == "DOC-CONS-101"]
    assert len(conflicts) == 1
    bboxes = [tuple(item["bbox"]) for item in conflicts[0]["evidence"]["items"]]
    assert set(bboxes) == {duration_box, other_box}


def test_blank_page_uses_full_page_bbox(qa_rules):
    model = make_model(body_texts={i: "内容" for i in range(1, 7)})
    model.pages = [_page(i) for i in (1, 2, 3, 5, 6)] + [
        _page(4, chars=0, images=0, tables=0)
    ]
    outcome = check_page_integrity(model, qa_rules)
    blanks = [f for f in outcome.findings if f.rule_code == "DOC-PAGE-001"]
    assert blanks
    assert all(item.bbox == PAGE_BBOX for item in blanks[0].evidence.items)


def test_hygiene_without_blocks_stays_none(qa_rules):
    model = make_model(body_texts={2: "项目经理：XXXX。"})
    outcome = check_hygiene(model, qa_rules)
    placeholders = [f for f in outcome.findings if f.rule_code == "DOC-HYG-001"]
    assert placeholders
    assert placeholders[0].evidence.items[0].bbox is None


def _span(page: int, text: str, bbox):
    return QaSpan(page=page, text=text, bbox=bbox, font="f", size=12, flags=0, color=0)


def test_tiny_subset_span_does_not_steal_full_block():
    """摘录真子集的单字 span 不得抢最小面积(量少看不见)。"""
    money_box = (0.12, 0.40, 0.88, 0.46)
    glyph_box = (0.86, 0.41, 0.88, 0.45)
    model = make_model(body_texts={3: "投标总价：人民币12,580,000元。"})
    model.blocks = [_block(
        3, "投标总价：人民币12,580,000元。", money_box,
        spans=(_span(3, "元", glyph_box),),
    )]
    assert locate_bbox(model, 3, "12,580,000元") == money_box


def test_ellipsis_page_suffix_and_fullwidth_comma_still_locate():
    """同形态第二实体: 目录「标题……页码」与中英文逗号差异仍能回到块框。"""
    toc_box = (0.10, 0.20, 0.72, 0.26)
    lang_box = (0.10, 0.40, 0.90, 0.52)
    model = make_model(body_texts={
        34: "6 现场试验与应用验证",
        2: "我单位承诺所提交资料真实、完整，项目实施过程中遵守数据安全。",
    })
    model.blocks = [
        _block(34, "6 现场试验与应用验证", toc_box),
        _block(2, "我单位承诺所提交资料真实、完整，项目实施过程中遵守数据安全。", lang_box),
    ]
    assert locate_bbox(model, 34, "6 现场试验与应用验证 …… 34") == toc_box
    assert locate_bbox(
        model, 2, "我单位承诺所提交资料真实、完整,项目实施过程中遵守数据安全、知识",
    ) == lang_box


def test_prefix_block_does_not_locate_a_longer_excerpt():
    """摘录比本页文字多出两个字以上时, 不能用前缀块画框。"""
    number_box = (0.08, 0.47, 0.32, 0.51)
    money_box = (0.10, 0.60, 0.55, 0.66)
    model = make_model(body_texts={
        222: "招标编号：FWFA00000114459",
        8: "投标总价：120万元",
    })
    model.blocks = [
        _block(222, "招标编号：FWFA00000114459", number_box),
        _block(8, "投标总价：120万元", money_box),
    ]
    assert locate_bbox(model, 222, "招标编号：FWFA000001144595填写说明：") is None
    assert locate_bbox(model, 8, "投标总价：120万元另计说明") is None
