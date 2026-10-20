""" 检索词面多源化：机制级防御测试。

问题形态: 检索词面被三个低质量单点垄断(固定窗口分词/类别先验闭集/页型闭集),
上游已结构化的高质量语义单元(LLM keywords、依据句式客体、子面触发词)不进检索链,
库内明词证据对特定条款不可见。

覆盖风险点:
1. 依据状语客体抽取(以A、B为依据/为准) —— 证据载体不在"提供"动词后
2. >6 字长专有名词不再被整体丢弃(固定窗口断崖)
3. 降级路径按语言边界切, 不产出跨词碎片
4. requirement.keywords 成一等分面并进词法槽
5. sub_requirements 的 ASCII 子面标签翻译为证据触发词
6. 词法槽只收干净短实义词, "要求前缀+词"/整句/ASCII 标签被排除
7. 账本原子获关键词级 kind, 跨条款注入按 kind 命中(本实例, 随 落地)

每条机制均有本案例(发票)与同形态第二例(社保/证书/中标通知书)两实体夹具。
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")

from app.core.ai.reviewer_parts.evidence_ledger.builder import collect_atoms_from_rows
from app.core.retriever.evidence_coverage import build_evidence_facets
from app.core.retriever.evidence_pipeline.contracts import RetrievalQuery
from app.core.retriever.evidence_pipeline.query_planner import (
    _is_lexical_worthy,
    plan_retrieval_channels,
)
from app.core.retriever.evidence_priors import CATEGORY_EVIDENCE_PRIORS
from app.core.retriever.facet_vocab import translate_facet_label
from app.core.retriever.query_decomposer import (
    _extract_objects,
    _tokenize_for_matching,
)

# 本案例实体: 业绩以合同、结算发票为依据
DESC_INVOICE = "须提供近三年同类项目业绩（以项目合同、结算发票为依据），按业绩数量排名。"
KW_INVOICE = ["近三年", "同类项目", "合同", "结算发票", "考核评价"]

# 同形态第二例: 业绩以中标通知书和竣工验收报告为准(证据载体同样在依据状语)
DESC_AWARD = "类似业绩以中标通知书和竣工验收报告为准。"


class TestBasisObjectExtraction:
    """风险点1: 依据状语句式的证据载体抽取(无'提供'动词)。"""

    def test_invoice_basis_object_captured(self):
        objects = _extract_objects(DESC_INVOICE)
        assert "结算发票" in objects, f"依据状语中的发票未抽取: {objects}"
        assert "项目合同" in objects

    def test_award_notice_basis_second_entity(self):
        """同形态第二例: 中标通知书/竣工验收报告。"""
        objects = _extract_objects(DESC_AWARD)
        assert "中标通知书" in objects, f"依据状语第二例失败: {objects}"
        assert "竣工验收报告" in objects

    def test_time_basis_not_mistaken_for_material(self):
        """'以合同签订日期为准'是时间基准, 不得当证据材料名。"""
        objects = _extract_objects("近三年内（以合同签订日期为准）的业绩")
        assert not any(o.endswith("日期") for o in objects), objects


class TestTokenizeNoFixedWindow:
    """风险点2/3: 长专有名词保留, 语言边界切分替代固定窗口。"""

    def test_long_cert_name_preserved(self):
        """>6 字无虚词的专有名词整体保留(此前被 2-6 字窗口丢弃)。"""
        assert _tokenize_for_matching("质量管理体系认证证书") == ["质量管理体系认证证书"]

    def test_basis_phrase_yields_core_noun_not_fragment(self):
        """'结算发票为依据'切出'结算发票'核心名, 不产'结算发票为'碎片。"""
        tokens = _tokenize_for_matching("结算发票为依据")
        assert "结算发票" in tokens
        assert all("为" not in t for t in tokens)

    def test_second_entity_long_material_preserved(self):
        """同形态第二例: 社保缴费证明材料(8 字)不被丢弃。"""
        assert _tokenize_for_matching("社保缴费证明材料") == ["社保缴费证明材料"]


class TestKeywordsBecomeFacets:
    """风险点4: requirement.keywords 成一等分面。"""

    def test_llm_keyword_becomes_facet_key(self):
        facets = build_evidence_facets(DESC_INVOICE, [], keywords=KW_INVOICE)
        keys = {f.key for f in facets}
        assert "结算发票" in keys, f"LLM 关键词未成分面: {keys}"

    def test_second_entity_keyword_facet(self):
        facets = build_evidence_facets(
            "须提供社会保险缴纳证明", [], keywords=["社保缴费证明", "参保人数"]
        )
        keys = {f.key for f in facets}
        assert "社保缴费证明" in keys and "参保人数" in keys


class TestFacetLabelTranslation:
    """风险点5: ASCII 子面标签翻译为证据触发词。"""

    def test_perf_evidence_label_translated_to_invoice(self):
        """perf_evidence 标签 -> 含'发票'的触发词分面, 而非原样 ASCII。"""
        facets = build_evidence_facets(DESC_INVOICE, ["perf_evidence"])
        keys = {f.key for f in facets}
        assert "发票" in keys, f"标签未翻译出发票: {keys}"
        assert "perf_evidence" not in keys, "ASCII 标签不应原样成分面"

    def test_personnel_social_label_second_entity(self):
        """同形态第二例: personnel_social -> 含'社保'。"""
        assert "社保" in translate_facet_label("personnel_social")
        facets = build_evidence_facets("人员社保要求", ["personnel_social"])
        keys = {f.key for f in facets}
        assert "社保" in keys and "personnel_social" not in keys


class TestLexicalSlotHygiene:
    """风险点6: 词法槽只收干净短实义词。"""

    def test_prefix_query_and_ascii_excluded(self):
        assert _is_lexical_worthy("结算发票") is True
        assert _is_lexical_worthy("近三年业绩 结算发票") is False  # 含空格(要求前缀+词)
        assert _is_lexical_worthy("enterprise_perf") is False  # 纯 ASCII 标签
        assert _is_lexical_worthy("要求: 提供发票") is False  # 含冒号整句

    def test_invoice_keyword_reaches_lexical_terms(self):
        facets = build_evidence_facets(
            DESC_INVOICE, ["perf_evidence"], keywords=KW_INVOICE
        )
        plan = plan_retrieval_channels(
            RetrievalQuery(
                document_id=1, description=DESC_INVOICE,
                category="PERF", facets=facets, is_mandatory=True,
            )
        )
        assert "结算发票" in plan.lexical_terms, f"发票未进词法槽: {plan.lexical_terms}"
        assert all(" " not in t for t in plan.lexical_terms), "词法槽混入带空格词"

    def test_second_entity_keyword_reaches_lexical(self):
        facets = build_evidence_facets(
            "须提供社会保险缴纳证明", ["personnel_social"],
            keywords=["社保缴费证明"],
        )
        plan = plan_retrieval_channels(
            RetrievalQuery(document_id=1, description="须提供社会保险缴纳证明",
                           category="STAFF", facets=facets)
        )
        assert any("社保" in t for t in plan.lexical_terms), plan.lexical_terms


class TestPerfPriorHasInvoice:
    """风险点(先验数据): PERF 先验补'发票'(合同的配套证据)。"""

    def test_perf_prior_includes_invoice(self):
        assert "发票" in CATEGORY_EVIDENCE_PRIORS["PERF"]


class TestLedgerCrossConditionKind:
    """风险点7: 账本原子获关键词级 kind, 跨条款注入按 kind 命中(本实例)。

 见: 关键词级 kind 让发现者以外的条款也能命中账本原子。
 """

    @staticmethod
    def _invoice_rows() -> list[dict]:
        return [{
            "bid_response_id": 100,
            "chunk_id": 7001,
            "page": 96,
            "document_id": 2,
            "matched_sub_query": "结算发票",
            "content": (
                "这是一份税务登记证的扫描件。其他信息: 结算发票；"
                "北京增值税专用发票；No 02482091"
            ),
            "chunk_type": "image_evidence",
        }]

    def test_invoice_atom_reaches_perf_requirement(self):
        """条款A(报价类)命中发票页, 条款B(业绩类, keywords 含结算发票)可见该原子。"""
        facets_a = build_evidence_facets(
            "投标报价须开具增值税发票", [], keywords=["增值税发票", "税率"]
        )
        facets_b = build_evidence_facets(
            DESC_INVOICE, ["perf_evidence"], keywords=KW_INVOICE
        )
        ledger = collect_atoms_from_rows(
            self._invoice_rows(),
            {100: facets_a, 200: facets_b},
            {100: 1, 200: 2},
        )
        atoms = ledger.atoms_for_facets(facets_b)
        assert any(
            "发票" in atom.kind for atom in atoms
        ), f"发票原子未按 kind 对业绩条款可见: {[a.kind for a in atoms]}"

    def test_second_entity_award_notice_atom(self):
        """同形态第二例: 中标通知书原子跨条款可见。"""
        rows = [{
            "bid_response_id": 300,
            "chunk_id": 7002,
            "page": 54,
            "document_id": 2,
            "matched_sub_query": "中标通知书",
            "content": "这是一份中标通知书扫描件。中标人: 某某公司 中标金额 120万元",
            "chunk_type": "image_evidence",
        }]
        facets_a = build_evidence_facets("投标文件完整性要求", [])
        facets_b = build_evidence_facets(
            DESC_AWARD, ["enterprise_perf"], keywords=["中标通知书", "业绩"]
        )
        ledger = collect_atoms_from_rows(
            rows, {300: facets_a, 400: facets_b}, {300: 3, 400: 4}
        )
        atoms = ledger.atoms_for_facets(facets_b)
        assert any("中标通知" in atom.kind for atom in atoms), [a.kind for a in atoms]
