""" 文档级材料库存与分级收紧。

覆盖风险点:
1. 词法 / 目录 / OCR 三源构建;
2. 空白模板不升级、标题+实质正文升级、OCR 仅佐证;
3. 分卷同物理页不串证 (document_id + bid_part);
4. skip_existing / 重启幂等 (单调并集, 等级只升不降)。
"""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")

from app.core.ai.reviewer_parts.evidence_ledger.grading import grade_atom
from app.core.retriever.material_inventory import (
    SOURCE_LEXICAL,
    SOURCE_OCR,
    SOURCE_TOC,
    MaterialFact,
    collect_noun_universe,
    fact_identity,
    facts_from_chunk,
    merge_fact_union,
)
from app.core.retriever.material_key import MATCH_STRONG, match_material_keys


def _req(**kwargs):
    payload = {
        "description": "",
        "keywords": None,
        "sub_requirements": None,
    }
    payload.update(kwargs)
    return SimpleNamespace(**payload)


def _chunk_fact(
    *,
    noun: str,
    chunk_id: int,
    document_id: int,
    bid_part: str | None,
    page: int,
    content: str,
    chunk_type: str,
    channel: str,
) -> MaterialFact | None:
    return facts_from_chunk(
        noun=noun,
        chunk_id=chunk_id,
        document_id=document_id,
        bid_part=bid_part,
        physical_page=page,
        content=content,
        chunk_type=chunk_type,
        source_channel=channel,
    )


class TestNounUniverse:
    def test_keywords_facets_and_basis_objects(self):
        nouns = collect_noun_universe([
            _req(
                description="同类项目业绩（以项目合同、结算发票为依据）",
                keywords='["结算发票"]',
                sub_requirements='["enterprise_perf"]',
            )
        ])
        joined = " ".join(nouns)
        assert "结算发票" in joined
        assert "项目合同" in joined


class TestThreeSources:
    def test_lexical_toc_ocr_union(self):
        lexical = _chunk_fact(
            noun="准入证",
            chunk_id=11,
            document_id=1,
            bid_part="commercial",
            page=40,
            content="河南省建设工程企业资质和资格管理系统专业准入证 编号YZ001",
            chunk_type="text",
            channel=SOURCE_LEXICAL,
        )
        toc = _chunk_fact(
            noun="授权委托书",
            chunk_id=12,
            document_id=1,
            bid_part="commercial",
            page=8,
            content="一、授权委托书 ........................ 8",
            chunk_type="text",
            channel=SOURCE_TOC,
        )
        ocr = _chunk_fact(
            noun="社保缴费证明",
            chunk_id=13,
            document_id=1,
            bid_part="commercial",
            page=90,
            content="社会保险缴费证明 编号(豫)社缴字20240001 参保单位<ORG_23>",
            chunk_type="image_evidence",
            channel=SOURCE_OCR,
        )
        assert lexical is not None and lexical.grade == "material"
        assert toc is not None and toc.grade == "mention"
        assert ocr is not None and ocr.grade == "material"
        merged = merge_fact_union([lexical, toc, ocr])
        assert len(merged) == 3
        channels = {item.source_channel for item in merged}
        assert channels == {SOURCE_LEXICAL, SOURCE_TOC, SOURCE_OCR}


class TestGradingTighten:
    def test_blank_template_stays_mention(self):
        assert grade_atom("image_evidence", "（粘贴处）", "准入证") == "mention"
        fact = _chunk_fact(
            noun="准入证",
            chunk_id=21,
            document_id=1,
            bid_part=None,
            page=3,
            content="准入证\n（空白模板）",
            chunk_type="image_evidence",
            channel=SOURCE_OCR,
        )
        assert fact is not None
        assert fact.grade == "mention"

    def test_title_plus_substance_upgrades(self):
        body = (
            "一、授权委托书\n"
            + '兹授权<PERSON_B>为我单位委托代理人，全权办理投标事宜。'
            + "授权期限自本文件签署之日起至招标结束。"
        )
        assert grade_atom("text", body, "授权委托书") == "material"

    def test_title_only_stays_mention(self):
        assert grade_atom("text", "一、授权委托书", "授权委托书") == "mention"

    def test_ocr_type_alone_does_not_upgrade(self):
        assert grade_atom("image_evidence", "本页为扫描件", "准入证") == "mention"

    def test_ocr_corroborates_with_identifier(self):
        content = "专业准入证 编号(豫)JZ准字00088 有效期至2028年"
        assert grade_atom("image_evidence", content, "准入证") == "material"


class TestEnvelopeIsolation:
    def test_same_physical_page_different_volumes_not_aliased(self):
        commercial = _chunk_fact(
            noun="准入证",
            chunk_id=31,
            document_id=101,
            bid_part="commercial",
            page=12,
            content="专业准入证 编号C-12",
            chunk_type="image_evidence",
            channel=SOURCE_OCR,
        )
        technical = _chunk_fact(
            noun="准入证",
            chunk_id=32,
            document_id=102,
            bid_part="technical",
            page=12,
            content="专业准入证 编号T-12",
            chunk_type="image_evidence",
            channel=SOURCE_OCR,
        )
        assert commercial is not None and technical is not None
        assert fact_identity(commercial) != fact_identity(technical)
        merged = merge_fact_union([commercial, technical])
        assert len(merged) == 2
        assert match_material_keys(
            "准入证",
            "准入证",
            left_identifier="C-12",
            right_identifier="T-12",
        ) != MATCH_STRONG


class TestIdempotentUnion:
    def test_rerun_does_not_duplicate_or_downgrade(self):
        first = _chunk_fact(
            noun="授权委托书",
            chunk_id=41,
            document_id=1,
            bid_part=None,
            page=8,
            content="授权委托书 兹授权<PERSON_3>办理投标事宜，授权范围包括签字盖章。",
            chunk_type="text",
            channel=SOURCE_LEXICAL,
        )
        weaker = MaterialFact(
            canonical_material_key=first.canonical_material_key,
            raw_noun=first.raw_noun,
            grade="mention",
            document_id=first.document_id,
            bid_part=first.bid_part,
            chunk_id=first.chunk_id,
            physical_page=first.physical_page,
            identifier=first.identifier,
            subject=first.subject,
            source_channel=SOURCE_TOC,
            excerpt="一、授权委托书",
        )
        once = merge_fact_union([first])
        twice = merge_fact_union(list(once) + [first, weaker])
        assert len(twice) == 1
        assert twice[0].grade == "material"
        assert twice[0].source_channel == SOURCE_LEXICAL
