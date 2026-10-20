"""复核层取舍合同: 高置信误报丢弃、真缺陷保留、低置信降级、调用失败原样保留。

夹具含两种检查器实体(编号 / 语言), 取舍不按规则码分支。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_scripts = Path(__file__).resolve().parents[2] / "scripts"
if str(_scripts) not in sys.path:
    sys.path.insert(0, str(_scripts))

from app.core.document_qa.contracts import (
    Evidence,
    EvidenceItem,
    Finding,
    QaCategory,
    QaCertainty,
    QaSeverity,
)
from app.core.document_qa.verify.contracts import coerce_page_verify_result
from app.core.document_qa.verify.evidence_pack import build_batches, confirmed_by_measurement
from app.core.document_qa.verify.finding_verifier import (
    apply_verdicts,
    normalize_confidence,
    verify_findings,
)
from tests.document_qa.conftest import make_model


def _finding(rule_code: str, page: int, excerpt: str) -> Finding:
    category = QaCategory.NUMBERING if "NUM" in rule_code else QaCategory.LANGUAGE
    return Finding(
        rule_code=rule_code,
        category=category,
        severity=QaSeverity.P2,
        certainty=QaCertainty.PROBABLE,
        title=rule_code,
        message=excerpt,
        evidence=Evidence(
            kind="single",
            items=(EvidenceItem(excerpt=excerpt, page=page),),
        ),
        suggestion="核对原文",
        anchor=f"{rule_code}:{page}",
    )


class _ScriptedClient:
    """按候选 rule_code 回固定判断, 并统计调用次数。"""

    def __init__(self, by_rule: dict[str, tuple[bool, float]], *, fail: bool = False) -> None:
        self.by_rule = by_rule
        self.fail = fail
        self.calls = 0

    async def chat_json(self, user_message: str, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("pool down")
        payload = json.loads(user_message[user_message.index("{"):])
        return {
            "verdicts": [
                {
                    "candidate_id": item["candidate_id"],
                    "is_real_defect": self.by_rule[item["rule_code"]][0],
                    "confidence": self.by_rule[item["rule_code"]][1],
                    "reason": "scripted",
                }
                for item in payload["candidates"]
            ],
        }


def test_same_page_candidates_share_one_batch() -> None:
    findings = [
        _finding("DOC-NUM-001", 4, "缺号 3"),
        _finding("DOC-LANG-001", 4, "括号未闭合"),
        _finding("DOC-NUM-002", 9, "编号倒置"),
    ]
    batches = build_batches(findings)
    assert len(batches) == 2
    assert len(batches[0][1]) == 2
    assert batches[0][0] == 4


def test_apply_verdicts_two_entities() -> None:
    findings = [
        _finding("DOC-NUM-001", 4, "缺号 3"),
        _finding("DOC-LANG-001", 4, "括号未闭合"),
    ]
    candidates = build_batches(findings)[0][1]
    verdicts = {
        candidates[0].candidate_id: (True, 0.95),
        candidates[1].candidate_id: (False, 0.91),
    }
    kept, counts = apply_verdicts(findings, verdicts, candidates, threshold=0.8)
    assert [item.rule_code for item in kept] == ["DOC-NUM-001"]
    assert kept[0].certainty == QaCertainty.PROBABLE
    assert counts["dropped"] == 1


def test_low_confidence_softens_without_dropping() -> None:
    findings = [_finding("DOC-LANG-003", 67, "半角分号")]
    candidates = build_batches(findings)[0][1]
    kept, counts = apply_verdicts(
        findings,
        {candidates[0].candidate_id: (False, normalize_confidence(40))},
        candidates,
        threshold=0.8,
    )
    assert counts["softened"] == 1
    assert kept[0].certainty == QaCertainty.POSSIBLE
    assert "建议人工核对" in kept[0].suggestion


def test_failed_call_keeps_original() -> None:
    findings = [_finding("DOC-NUM-002", 9, "编号倒置")]
    candidates = build_batches(findings)[0][1]
    kept, counts = apply_verdicts(
        findings,
        {},
        candidates,
        threshold=0.8,
        keep_ids={candidates[0].candidate_id},
    )
    assert counts["kept"] == 1
    assert kept[0].certainty == QaCertainty.PROBABLE


@pytest.mark.asyncio
async def test_verify_merges_page_and_filters() -> None:
    model = make_model(body_texts={
        4: "章节（1）（2）（3）连续, 并无括号未闭合。",
        9: "1.2 之后直接是 1.1, 编号顺序倒置。",
    })
    findings = [
        _finding("DOC-LANG-001", 4, "并无括号未闭合"),
        _finding("DOC-NUM-002", 9, "编号顺序倒置"),
    ]
    client = _ScriptedClient({
        "DOC-LANG-001": (False, 0.96),
        "DOC-NUM-002": (True, 0.9),
    })
    kept, stats = await verify_findings(model, findings, project_id=1, client=client)
    assert client.calls == 2
    assert [item.rule_code for item in kept] == ["DOC-NUM-002"]
    assert stats["dropped"] == 1
    assert stats["degraded"] is False


def test_closed_measurement_keeps_numbering_and_page_size() -> None:
    """闭合度量: 编号缺号与纸张尺寸是两种实体, 模型高置信否定也不能删。"""
    model = make_model(body_texts={9: "1.2 之后是 1.4。", 47: "正文仍是正常段落。"})
    numbering = _finding("DOC-NUM-001", 9, "章节编号序列缺少 1.3")
    numbering = Finding(
        rule_code=numbering.rule_code,
        category=QaCategory.NUMBERING,
        severity=numbering.severity,
        certainty=numbering.certainty,
        title=numbering.title,
        message=numbering.message,
        evidence=numbering.evidence,
        anchor=numbering.anchor,
        closed_measurement=True,
    )
    page_size = Finding(
        rule_code="DOC-PAGE-003",
        category=QaCategory.INTEGRITY,
        severity=QaSeverity.P3,
        certainty=QaCertainty.POSSIBLE,
        title="纸张尺寸不一致",
        message="第47页尺寸明显不同",
        evidence=Evidence(
            kind="single",
            items=(EvidenceItem(excerpt="第47页 612x792pt", page=47),),
        ),
        anchor="size:47",
        closed_measurement=True,
    )
    assert confirmed_by_measurement(model, numbering) is True
    assert confirmed_by_measurement(model, page_size) is True


@pytest.mark.asyncio
async def test_closed_measurement_is_not_sent_to_model() -> None:
    model = make_model(body_texts={9: "1.2 之后是 1.4。", 39: "方案正文。"})
    numbering = Finding(
        rule_code="DOC-NUM-001",
        category=QaCategory.NUMBERING,
        severity=QaSeverity.P1,
        certainty=QaCertainty.CERTAIN,
        title="章节编号不连续",
        message="缺少 1.3",
        evidence=Evidence(
            kind="sequence",
            items=(EvidenceItem(excerpt="1.2 目标", page=9),),
        ),
        anchor="num:1.3",
        closed_measurement=True,
    )
    note = Finding(
        rule_code="DOC-HYG-003",
        category=QaCategory.HYGIENE,
        severity=QaSeverity.P2,
        certainty=QaCertainty.CERTAIN,
        title="PDF 批注残留",
        message="第39页存在批注",
        evidence=Evidence(
            kind="single",
            items=(EvidenceItem(excerpt="内部批注：确认后删除", page=39),),
        ),
        anchor="ann:39",
        closed_measurement=True,
    )
    client = _ScriptedClient({
        "DOC-NUM-001": (False, 0.99),
        "DOC-HYG-003": (False, 0.99),
    })
    kept, stats = await verify_findings(
        model, [numbering, note], project_id=1, client=client,
    )
    assert client.calls == 0
    assert [item.rule_code for item in kept] == ["DOC-NUM-001", "DOC-HYG-003"]
    assert stats["dropped"] == 0
    assert kept[0].certainty == QaCertainty.CERTAIN


def test_excerpt_supported_punctuation_is_not_dropped() -> None:
    """单处半角标点可以被高置信否定删除。同一摘录里三处仍挡住删除。"""
    comma = _finding("DOC-LANG-003", 12, "数据采集,处理")
    closed = _finding("DOC-LANG-003", 9, "合同,并承担。价款,含税。工期,日历天")
    closed = Finding(
        rule_code=closed.rule_code,
        category=closed.category,
        severity=closed.severity,
        certainty=closed.certainty,
        title=closed.title,
        message=closed.message,
        evidence=closed.evidence,
        suggestion=closed.suggestion,
        anchor=closed.anchor,
        closed_measurement=True,
    )
    fake_bracket = _finding("DOC-LANG-001", 4, "并无括号未闭合")
    comma_batch = build_batches([comma])[0][1]
    closed_batch = build_batches([closed])[0][1]
    bracket_batch = build_batches([fake_bracket])[0][1]
    kept_comma, counts_comma = apply_verdicts(
        [comma],
        {comma_batch[0].candidate_id: (False, 0.95)},
        comma_batch,
        threshold=0.8,
    )
    kept_closed, counts_closed = apply_verdicts(
        [closed],
        {closed_batch[0].candidate_id: (False, 0.95)},
        closed_batch,
        threshold=0.8,
    )
    kept_bracket, counts_bracket = apply_verdicts(
        [fake_bracket],
        {bracket_batch[0].candidate_id: (False, 0.95)},
        bracket_batch,
        threshold=0.8,
    )
    assert counts_comma["dropped"] == 1
    assert kept_comma == []
    assert counts_closed["kept"] == 1
    assert kept_closed[0].certainty == QaCertainty.PROBABLE
    assert counts_bracket["dropped"] == 1
    assert kept_bracket == []


def test_measurement_does_not_cover_language_findings() -> None:
    model = make_model(body_texts={18: " "})
    assert confirmed_by_measurement(model, _finding("DOC-LANG-001", 18, "括号")) is False


@pytest.mark.asyncio
async def test_empty_page_integrity_is_not_overruled() -> None:
    model = make_model(body_texts={18: " "})
    finding = Finding(
        rule_code="DOC-PAGE-001",
        category=QaCategory.INTEGRITY,
        severity=QaSeverity.P2,
        certainty=QaCertainty.PROBABLE,
        title="空白页",
        message="第18页无任何文本",
        evidence=Evidence(
            kind="single",
            items=(EvidenceItem(excerpt="第18页为空白页", page=18),),
        ),
        anchor="blank:18",
    )
    client = _ScriptedClient({"DOC-PAGE-001": (False, 0.99)})
    kept, stats = await verify_findings(model, [finding], project_id=1, client=client)
    assert client.calls == 0
    assert kept[0].certainty == QaCertainty.PROBABLE
    assert stats["dropped"] == 0


@pytest.mark.asyncio
async def test_verify_pool_error_fails_open() -> None:
    model = make_model(body_texts={4: "原文正常。"})
    findings = [
        _finding("DOC-LANG-001", 4, "原文正常"),
        _finding("DOC-NUM-001", 4, "原文正常"),
    ]
    client = _ScriptedClient({}, fail=True)
    kept, stats = await verify_findings(model, findings, project_id=1, client=client)
    assert len(kept) == 2
    assert kept[0].certainty == QaCertainty.PROBABLE
    assert stats["degraded"] is True
    assert stats["failed_calls"] == 1


def test_verify_response_schema_builds() -> None:
    from app.core.ai.constrained_decoding import build_response_format
    from app.core.document_qa.verify.contracts import PageVerifyResult

    fmt = build_response_format(PageVerifyResult)
    assert fmt["type"] == "json_schema"
    assert "verdicts" in fmt["json_schema"]["schema"]["properties"]


def test_coerce_accepts_list_and_alias_keys() -> None:
    listed = coerce_page_verify_result([
        {"candidate_id": "p4-1", "is_real_defect": False, "confidence": 0.9, "detail": "抽取错位"},
    ])
    aliased = coerce_page_verify_result({
        "findings": [
            {"id": "p9-2", "is_real_defect": "true", "confidence": 80, "reason": "原文缺号"},
        ],
    })
    assert listed.verdicts[0].candidate_id == "p4-1"
    assert listed.verdicts[0].is_real_defect is False
    assert listed.verdicts[0].reason == "抽取错位"
    assert aliased.verdicts[0].candidate_id == "p9-2"
    assert aliased.verdicts[0].is_real_defect is True


def test_verify_node_registered_mechanical() -> None:
    from seed_llm_registry import ALL_NODES

    node = next(item for item in ALL_NODES if item.node_key == "document_qa.verify")
    assert node.node_class == "mechanical"
