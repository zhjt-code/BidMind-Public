""" 防御测试：检索链路三重失效的泛化修复

覆盖风险点:
1. 复合要求分解失败(多动词/长客体/括号插入语) -> 整条长查询被模板词稀释
2. 关键词仅产出泛化主体 -> 全局图片检索被文档前部无关扫描件占满
3. 类别证据先验闭集 -> 替代开放同义词表的泛化机制
4. 低信息密度惩罚与相关性排序 -> 替代页码序截断
5. 检索饥饿决策 -> 否决项误判"未响应"前的最后防线
6. not-found 响应挂载检索命中 -> 故障可回放

测试数据使用项目1488要求15507的真实描述(已确认的故障样本)。
"""

import asyncio

import pytest

from app.core.parser.toc_parser import TocParser
from app.core.retriever.evidence_priors import (
    CATEGORY_EVIDENCE_PRIORS,
    StarvationFinding,
    _evaluate_starvation,
    augment_keywords_with_priors,
    augment_queries_with_priors,
    detect_retrieval_starvation,
    format_starvation_note,
    get_prior_phrases,
)
from app.core.retriever.evidence_ranker import (
    _apply_penalty_and_sort,
    dedupe_by_content_prefix,
    is_low_information_content,
)
from app.core.retriever.query_decomposer import (
    GENERIC_SUBJECT_TERMS,
    QueryDecomposer,
)
from app.core.retriever.semantic_retriever import SearchResult
from app.models.requirement import RequirementCategory

# 项目1488要求15507的真实描述(故障样本)
REQ_15507_DESC = (
    "投标人需提供近3年内（以招标文件发布日或合同签订日期为准）至少2项类似项目业绩，"
    "合同金额不低于50万元，项目类型包括油田防腐药剂或防腐工具性能测试、井筒腐蚀类、"
    "井筒腐蚀防护类或其它相类似研究项目，需提供合同或验收证明等有效证明材料，"
    "业绩信息需包含招标人、项目名称、管理规模、开完工日期、在服务或已完状态、服务质量等关键要素。"
)
FORM_TITLE_DESC = "正在施工和新承接项目情况表"
FORM_TITLE_TOC = "正在施工的和新承接的项目情况表"


def _make_result(
    chunk_id: int,
    content: str,
    page_num: int = 1,
    similarity: float = 0.0,
) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        document_id=1,
        content=content,
        page_num=page_num,
        similarity=similarity,
    )


class TestDecomposeMultiVerb:
    """风险点1: 复合要求分解失败"""

    def setup_method(self):
        self.decomposer = QueryDecomposer()

    def test_req_15507_decomposes_to_multiple_queries(self):
        """故障样本必须分解出多个子查询(修复前仅1个)"""
        queries = self.decomposer.decompose(REQ_15507_DESC)
        assert len(queries) >= 2, f"分解失败,仅得到: {queries}"

    def test_req_15507_captures_second_verb_object(self):
        """第二个'需提供'的客体(合同或验收证明)必须被捕获"""
        queries = self.decomposer.decompose(REQ_15507_DESC)
        joined = " ".join(queries)
        assert "合同或验收证明" in joined or "验收证明" in joined, (
            f"多动词客体丢失: {queries}"
        )

    def test_paren_content_stripped_from_objects(self):
        """括号插入语剥离后,长客体可通过长度过滤"""
        queries = self.decomposer.decompose(REQ_15507_DESC)
        joined = " ".join(queries)
        assert "类似项目业绩" in joined, f"业绩客体丢失: {queries}"
        assert "以招标文件发布日" not in joined, "括号内容未剥离"

    def test_cartesian_pattern_not_regressed(self):
        """既有笛卡尔积句式不回归"""
        desc = "项目经理、技术负责人、安全负责人均需提供身份证和社保缴费证明"
        queries = self.decomposer.decompose(desc)
        assert len(queries) == 6
        assert "项目经理 身份证" in queries
        assert "安全负责人 社保缴费证明" in queries


class TestExtractKeywordsDegeneralization:
    """风险点2: 关键词仅产出泛化主体"""

    def setup_method(self):
        self.decomposer = QueryDecomposer()

    def test_req_15507_keywords_not_only_generic(self):
        """故障样本关键词不能仅为'投标人'(修复前唯一产出)"""
        keywords = self.decomposer.extract_keywords(REQ_15507_DESC)
        assert keywords, "关键词不能为空"
        assert keywords != ["投标人"], "关键词退化为泛化主体"
        assert all(kw not in GENERIC_SUBJECT_TERMS for kw in keywords), (
            f"泛化主体未剔除: {keywords}"
        )

    def test_generic_only_description_falls_back_to_nouns(self):
        """仅含泛化主体的描述强制走名词降级提取"""
        keywords = self.decomposer.extract_keywords("投标人需具备良好商业信誉")
        assert keywords, "名词降级提取未生效"
        assert "投标人" not in keywords

    def test_long_object_phrase_tokenized(self):
        """长客体短语切分为可字面匹配的短词"""
        keywords = self.decomposer.extract_keywords(REQ_15507_DESC)
        assert any(len(kw) <= 6 for kw in keywords), (
            f"长短语未切分,无法用于字面包含过滤: {keywords}"
        )

    def test_form_title_keywords_include_stable_anchors(self):
        """表名类短要求必须产出可字面匹配的稳定锚点。"""
        keywords = self.decomposer.extract_keywords(FORM_TITLE_DESC)
        assert "正在施工" in keywords
        assert "新承接" in keywords
        assert "项目情况表" in keywords

    def test_form_title_decompose_adds_anchor_queries(self):
        """表名类短要求不能只用整句长 query 检索。"""
        queries = self.decomposer.decompose(FORM_TITLE_DESC)
        assert FORM_TITLE_DESC in queries
        assert any("正在施工" == q for q in queries)
        assert any("新承接" == q for q in queries)
        assert any("项目情况表" == q for q in queries)


class TestTocLookupTolerance:
    """目录导航对表名助词差异的容错。"""

    def test_form_title_lookup_ignores_particle_difference(self):
        """少量'的'字差异不应导致目录导航失效。"""
        toc_map = {
            "version": 2,
            "enabled": True,
            "keywords": {
                FORM_TITLE_TOC: [
                    {
                        "title": FORM_TITLE_TOC,
                        "printed_page": 472,
                        "physical_page": 472,
                    }
                ]
            },
        }

        assert TocParser().lookup_physical_pages(FORM_TITLE_DESC, toc_map) == [472]


class TestCategoryEvidencePriors:
    """风险点3: 类别证据先验闭集"""

    def test_all_six_categories_covered(self):
        """先验表必须覆盖全部6类(闭集完整性)"""
        expected = {c.name for c in RequirementCategory}
        assert set(CATEGORY_EVIDENCE_PRIORS.keys()) == expected

    def test_get_prior_phrases_accepts_enum_name_value(self):
        """enum / name字符串 / value字符串三种入参等价"""
        by_enum = get_prior_phrases(RequirementCategory.PERF)
        by_name = get_prior_phrases("PERF")
        by_value = get_prior_phrases("performance")
        assert by_enum == by_name == by_value
        assert "合同" in by_enum

    def test_unknown_category_returns_empty(self):
        """None/未知类别退化为空列表(全链路无先验行为)"""
        assert get_prior_phrases(None) == []
        assert get_prior_phrases("nonexistent") == []

    def test_augment_queries_respects_caps(self):
        """查询增强受 max_added 与 max_total 双重上限保护"""
        base = [f"q{i}" for i in range(18)]
        out = augment_queries_with_priors(
            base, REQ_15507_DESC, RequirementCategory.PERF, max_total=20
        )
        assert len(out) <= 20
        assert out[:18] == base

    def test_augment_queries_adds_prior_phrases(self):
        out = augment_queries_with_priors(
            [REQ_15507_DESC], REQ_15507_DESC, RequirementCategory.PERF
        )
        assert len(out) >= 2
        assert any("合同" in q for q in out[1:])

    def test_augment_keywords_merges_priors(self):
        out = augment_keywords_with_priors(["既有词"], RequirementCategory.PERF)
        assert "既有词" in out
        assert "验收证明" in out

    def test_augment_with_none_category_is_noop(self):
        queries = ["q1"]
        assert augment_queries_with_priors(queries, "desc", None) == queries
        assert augment_keywords_with_priors(queries, None) == queries


class TestStarvationDecision:
    """风险点5: 检索饥饿决策(纯函数三路径)"""

    def test_starved_when_candidates_uncovered(self):
        """文档存在证据页但上下文未覆盖 -> 判定饥饿"""
        finding = _evaluate_starvation(
            candidate_pages={36, 37, 39, 40, 41},
            covered_pages={2, 3, 5},
            phrases=["合同", "业绩"],
        )
        assert finding is not None
        assert finding.uncovered_pages == (36, 37, 39, 40, 41)

    def test_not_starved_when_covered(self):
        """证据页已被上下文覆盖 -> 不判定饥饿"""
        finding = _evaluate_starvation(
            candidate_pages={36, 37},
            covered_pages={36, 37, 40},
            phrases=["合同"],
        )
        assert finding is None

    def test_not_starved_below_threshold(self):
        """未覆盖页数低于阈值 -> 不判定饥饿(防噪)"""
        finding = _evaluate_starvation(
            candidate_pages={36},
            covered_pages=set(),
            phrases=["合同"],
            min_uncovered_pages=2,
        )
        assert finding is None

    def test_note_format_bounded(self):
        """饥饿标记文本含页码且长度受限(relevance_note为varchar(500))"""
        from app.core.retriever.evidence_priors import STARVATION_NOTE_PREFIX

        finding = StarvationFinding(
            uncovered_pages=tuple(range(1, 100)),
            candidate_page_count=99,
            phrases_used=("合同", "验收证明"),
        )
        note = format_starvation_note(finding)
        assert note.startswith(STARVATION_NOTE_PREFIX)
        assert "合同" in note
        assert len(note) <= 490

    def test_note_is_user_friendly_without_technical_jargon(self):
        """用户反馈: relevance_note 前端原样展示, 禁止技术黑话(检索/上下文/chunk)。"""
        finding = StarvationFinding(
            uncovered_pages=(22, 23, 24),
            candidate_page_count=3,
            phrases_used=("签字", "盖章"),
        )
        note = format_starvation_note(finding)
        for jargon in ("检索", "上下文", "chunk", "饥饿"):
            assert jargon not in note, f"用户可见文案不得含技术词'{jargon}': {note}"
        assert "人工" in note and "22,23,24" in note

    def test_is_starvation_note_accepts_new_and_legacy_prefixes(self):
        """机器判定单源: 新前缀与存量旧前缀均须识别, 普通文本不误判。"""
        from app.core.retriever.evidence_priors import (
            STARVATION_NOTE_PREFIX,
            is_starvation_note,
        )

        assert is_starvation_note(f"{STARVATION_NOTE_PREFIX} 投标文件第5页...") is True
        assert is_starvation_note("[检索饥饿] 文档第5页存在...") is True, (
            "存量数据库 relevance_note 仍为旧前缀, 判定必须兼容"
        )
        assert is_starvation_note("投标人未提供相关材料") is False
        assert is_starvation_note(None) is False
        assert is_starvation_note("") is False

    def test_ratio_guard_suppresses_indiscriminate_phrase(self):
        """要素2: 候选页占比过高(无区分度短语,如遍布全文的盖章) -> 抑制误报"""
        finding = _evaluate_starvation(
            candidate_pages=set(range(1, 201)),
            covered_pages={1, 2, 3},
            phrases=["盖章", "签字"],
            min_uncovered_pages=3,
            candidate_total=300,
            total_doc_pages=400,  # 75% > 35%
            max_candidate_ratio=0.35,
        )
        assert finding is None

    def test_ratio_guard_passes_low_ratio_bug485(self):
        """要素2/回归: 业绩材料低占比 -> 护栏放行,仍判饥饿(不误伤真信号)"""
        finding = _evaluate_starvation(
            candidate_pages={36, 37, 39, 40, 41, 42, 43, 44},
            covered_pages={2, 3, 5},
            phrases=["合同", "业绩"],
            min_uncovered_pages=3,
            candidate_total=8,
            total_doc_pages=200,  # 4% << 35%
            max_candidate_ratio=0.35,
        )
        assert finding is not None
        assert 44 in finding.uncovered_pages

    def test_ratio_guard_uses_candidate_total_not_len(self):
        """占比分子用真实计数 candidate_total, 不被明细 limit 截断误导"""
        # 明细仅取回2页(被limit截断),但真实候选500/全文600=83% -> 仍抑制
        finding = _evaluate_starvation(
            candidate_pages={10, 20},
            covered_pages=set(),
            phrases=["盖章"],
            min_uncovered_pages=2,
            candidate_total=500,
            total_doc_pages=600,
            max_candidate_ratio=0.35,
        )
        assert finding is None

    def test_ratio_guard_disabled_when_ratio_ge_one(self):
        """max_candidate_ratio>=1.0 关闭占比护栏(向后兼容旧默认行为)"""
        finding = _evaluate_starvation(
            candidate_pages=set(range(1, 51)),
            covered_pages=set(),
            phrases=["盖章"],
            min_uncovered_pages=3,
            candidate_total=50,
            total_doc_pages=50,
            max_candidate_ratio=1.0,
        )
        assert finding is not None


class _FakeResult:
    """模拟 SQLAlchemy 执行结果: 按需提供 scalar()/all()"""

    def __init__(self, scalar_value=None, rows=None):
        self._scalar = scalar_value
        self._rows = rows or []

    def scalar(self):
        return self._scalar

    def all(self):
        return self._rows


class _FakeSession:
    """按调用顺序回放预设结果, 并记录执行过的 statement(供SQL断言)"""

    def __init__(self, results):
        self._results = list(results)
        self.executed = []

    async def execute(self, stmt):
        self.executed.append(stmt)
        return self._results.pop(0)


class TestStarvationDbGate:
    """检索饥饿 DB 闸门: 要素1要求级约束 + 端到端占比护栏(粒度错配修复)"""

    def test_low_ratio_bug485_still_starved(self):
        """端到端: 业绩低占比 -> 编排正确 + 护栏放行 + 判饥饿(回归)"""
        session = _FakeSession([
            _FakeResult(scalar_value=8),     # candidate_total
            _FakeResult(scalar_value=200),   # total_doc_pages
            _FakeResult(rows=[(p,) for p in (36, 37, 39, 40, 41, 42, 43, 44)]),
        ])
        finding = asyncio.run(detect_retrieval_starvation(
            session,
            document_id=1,
            category=RequirementCategory.PERF,
            covered_pages={2, 3, 5},
            requirement_keywords=["业绩", "合同"],
            min_uncovered_pages=3,
            max_candidate_ratio=0.35,
        ))
        assert finding is not None
        assert 36 in finding.uncovered_pages
        assert len(session.executed) == 3

    def test_high_ratio_indiscriminate_suppressed(self):
        """端到端: 候选占比过高(盖章遍布全文) -> 抑制告警"""
        session = _FakeSession([
            _FakeResult(scalar_value=680),   # candidate_total
            _FakeResult(scalar_value=700),   # total_doc_pages (97%)
            _FakeResult(rows=[(p,) for p in range(1, 201)]),
        ])
        finding = asyncio.run(detect_retrieval_starvation(
            session,
            document_id=1,
            category=RequirementCategory.FORMAT,
            covered_pages={1, 2, 3},
            requirement_keywords=["盖章"],
            min_uncovered_pages=3,
            max_candidate_ratio=0.35,
        ))
        assert finding is None

    def test_requirement_keyword_enters_candidate_query(self):
        """要素1: 要求关键词注入候选查询(把类别级证据池收窄到要求级)"""
        session = _FakeSession([
            _FakeResult(scalar_value=3),
            _FakeResult(scalar_value=200),
            _FakeResult(rows=[(36,), (37,), (40,)]),
        ])
        asyncio.run(detect_retrieval_starvation(
            session,
            document_id=1,
            category=RequirementCategory.PERF,
            covered_pages=set(),
            requirement_keywords=["防腐蚀"],  # 非PERF类别短语, 可区分来源
            min_uncovered_pages=2,
            max_candidate_ratio=0.9,
        ))
        compiled = session.executed[0].compile()
        param_values = [str(v) for v in compiled.params.values()]
        assert any("防腐蚀" in v for v in param_values), (
            f"要求关键词未进入候选查询: {param_values}"
        )

    def test_empty_keywords_degrades_to_category_only(self):
        """要素1退化: 空关键词时仅用类别短语, 不报错且不注入外部词"""
        session = _FakeSession([
            _FakeResult(scalar_value=8),
            _FakeResult(scalar_value=200),
            _FakeResult(rows=[(p,) for p in (36, 37, 39, 40, 41, 42, 43, 44)]),
        ])
        finding = asyncio.run(detect_retrieval_starvation(
            session,
            document_id=1,
            category=RequirementCategory.PERF,
            covered_pages={2, 3},
            requirement_keywords=None,
            min_uncovered_pages=3,
            max_candidate_ratio=0.35,
        ))
        assert finding is not None
        assert len(session.executed) == 3
        compiled = session.executed[0].compile()
        param_values = [str(v) for v in compiled.params.values()]
        assert not any("防腐蚀" in v for v in param_values)


class TestEvidenceRanker:
    """风险点4: 低信息惩罚与相关性排序"""

    def test_toc_dotted_page_is_low_information(self):
        """目录点线页识别(故障样本中排名第2/4的污染chunk形态)"""
        toc = "六、营业执照 ......................................... 18"
        assert is_low_information_content(toc) is True
        catalog = "目 录\n一、投标函 .............................. 3"
        assert is_low_information_content(catalog) is True

    def test_normal_clause_not_low_information(self):
        clause = (
            "8.2 施工业绩合同签订时间 <ORG_ENERGY_2>科YT1井地质封存CO2高密相驱替"
            "综合实验项目,合同金额为人民币壹佰贰拾万元整,验收结论为合格。"
        )
        assert is_low_information_content(clause) is False

    def test_dedupe_removes_repeated_annotation(self):
        """同页重复标注段去重(故障样本p32有3条相同'注:...'段)"""
        dup = "注：页面左上角标注5技术人员-陈岑,页脚标有页码30,与题干所述第32页不一致"
        results = [
            _make_result(1, dup, page_num=32),
            _make_result(2, dup, page_num=32),
            _make_result(3, dup, page_num=32),
            _make_result(4, "其他内容段落", page_num=33),
        ]
        deduped = dedupe_by_content_prefix(results)
        assert len(deduped) == 2
        assert deduped[0].chunk_id == 1

    def test_penalty_and_sort_prefers_relevant_evidence(self):
        """业绩合同chunk(高相似度)必须排在身份证chunk(低相似度)与目录页之前"""
        perf = _make_result(10, "测试分析合同扫描件,佐证CO2地质封存项目业绩", page_num=42)
        idcard = _make_result(20, "身份证扫描件,证明投标人身份及任职资格", page_num=8)
        toc = _make_result(30, "八、与评分有关资料 ................... 19", page_num=2)
        similarities = {10: 0.68, 20: 0.55, 30: 0.66}
        ranked = _apply_penalty_and_sort([idcard, toc, perf], similarities)
        assert ranked[0].chunk_id == 10, "业绩证据未排首位"
        assert ranked[0].similarity == pytest.approx(0.68)
        assert ranked[-1].chunk_id == 30, "目录页未被惩罚到末位"


class TestNotFoundObservability:
    """风险点6: not-found 响应挂载检索命中"""

    def _make_extractor(self):
        from app.core.extractor.bid_extractor import BidExtractor

        return BidExtractor.__new__(BidExtractor)

    def test_not_found_attaches_retrieval_chunks(self):
        extractor = self._make_extractor()
        results = [
            _make_result(101, "内容A", page_num=5, similarity=0.7),
            _make_result(102, "内容B", page_num=8, similarity=0.6),
        ]
        results[0].role = "primary"
        response = extractor._create_not_found_response_by_id(
            1, 2, search_results=results
        )
        pending = response._pending_retrieval_chunks
        assert len(pending) == 2
        assert pending[0]["chunk_id"] == 101
        assert pending[0]["role"] == "primary"
        assert pending[1]["role"] == "primary"  # 未标记时默认 primary

    def test_not_found_without_results_keeps_empty(self):
        """无检索结果时保持空挂载(早退路径/异常兜底路径兼容)"""
        extractor = self._make_extractor()
        response = extractor._create_not_found_response_by_id(1, 2)
        assert response._pending_retrieval_chunks == []
        assert response.content == "[未找到响应]"
