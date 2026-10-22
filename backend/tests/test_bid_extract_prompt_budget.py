""" token 补强防御测试: 提取送模前的分面保底排序与硬上限适配。

覆盖风险点:
1. 多分面条款的弱势分面代表必须进入可见集队首, 不被强势分面挤出 top_k 主槽
 (本案例夹具: QUAL 营业执照挤掉授权委托书; 同形态第二例: STAFF 证书挤掉社保);
2. 少于 2 个分面时禁止重排——保护 RRF 融合序, 词法命中(similarity=0)不得被挤到队尾;
3. 发送前硬上限适配: round 0 通过即原样发送; 超限逐轮收缩; 收缩到底仍超返回 None;
4. _call_llm_extract 硬上限不可满足时返回 ExtractionFailedSignal(input_oversize),
 不得发起 LLM 请求(走"[提取失败待复核]"合同, 不伪装成业务未找到);
5. 硬帽下沉后 reviewer_parts.evidence_pack_budget 的既有导入路径保持可用。
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")

from app.core.ai import input_budget
from app.core.config import settings
from app.core.extractor.bid_extract_prompt_budget import (
    OVERSIZE_REASON,
    fit_extract_reduction_round,
    order_results_facet_first,
)
from app.core.extractor.bid_extractor import BidExtractor
from app.core.extractor.extraction_failure import ExtractionFailedSignal
from app.core.retriever.evidence_coverage import EvidenceFacet
from app.core.retriever.semantic_retriever import SearchResult
from app.schemas.bid_response import ExtractedBidResponse


def _sr(
    chunk_id: int,
    content: str,
    similarity: float = 0.5,
    page: int = 1,
) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        document_id=1,
        content=content,
        page_num=page,
        similarity=similarity,
        role="primary",
    )


# ---------------------------------------------------------------------------
# 1. 分面保底排序
# ---------------------------------------------------------------------------


class TestOrderResultsFacetFirst:
    def test_weak_authorization_facet_enters_head(self):
        """本案例夹具: 8 条营业执照命中霸占前排, 授权委托书唯一命中在第 9 位。

 排序后授权委托书代表必须进入前 2 槽(分面数=2), 否则 top_k=8 的主槽
 全被营业执照占据, 弱势分面在预算截断中丢失(症状)。
 """
        facets = (
            EvidenceFacet(key="营业执照", query="资质证明 营业执照"),
            EvidenceFacet(key="授权委托书", query="资质证明 授权委托书"),
        )
        pool = [
            _sr(i, f"营业执照及资质等级证明材料第{i}页", similarity=0.9 - i * 0.01)
            for i in range(1, 9)
        ]
        pool.append(_sr(9, "法定代表人授权委托书扫描件", similarity=0.4, page=7))

        ordered = order_results_facet_first(pool, facets)

        head_ids = [r.chunk_id for r in ordered[:2]]
        assert 9 in head_ids, "授权委托书代表必须进入前 len(facets) 槽"
        assert head_ids[0] == 1, "强势分面代表保持原有首位"
        # 其余条目保持原相对顺序
        rest_ids = [r.chunk_id for r in ordered[2:]]
        assert rest_ids == [2, 3, 4, 5, 6, 7, 8]

    def test_second_shape_staff_social_security(self):
        """同形态第二例: STAFF 证书+社保+业绩, 社保唯一命中在第 10 位。"""
        facets = (
            EvidenceFacet(key="资格证书", query="人员 资格证书"),
            EvidenceFacet(key="社保缴费证明", query="人员 社保缴费证明"),
            EvidenceFacet(key="人员业绩", query="人员 人员业绩"),
        )
        pool = [
            _sr(i, f"项目人员资格证书汇总第{i}页", similarity=0.9 - i * 0.01)
            for i in range(1, 6)
        ]
        pool.append(_sr(6, "近三年人员业绩一览", similarity=0.6))
        pool.extend(
            _sr(i, f"人员资格证书附件第{i}页", similarity=0.5) for i in range(7, 10)
        )
        pool.append(_sr(10, "社保缴费证明单位缴费记录", similarity=0.3))

        ordered = order_results_facet_first(pool, facets)

        head_ids = {r.chunk_id for r in ordered[:3]}
        assert head_ids == {1, 6, 10}, "三个分面各自的首条命中都必须在前 3 槽"

    def test_single_facet_preserves_rrf_order(self):
        """单分面禁止重排: 词法命中 similarity=0 在首位时必须留在首位。"""
        facets = (EvidenceFacet(key="营业执照", query="营业执照"),)
        lexical_hit = _sr(1, "营业执照统一社会信用代码", similarity=0.0)
        dense_hit = _sr(2, "营业执照复印件加盖公章", similarity=0.9)
        ordered = order_results_facet_first([lexical_hit, dense_hit], facets)
        assert [r.chunk_id for r in ordered] == [1, 2]

    def test_no_facet_hit_and_empty_inputs(self):
        facets = (
            EvidenceFacet(key="审计报告", query="审计报告"),
            EvidenceFacet(key="纳税证明", query="纳税证明"),
        )
        pool = [_sr(1, "与分面无关的内容"), _sr(2, "另一段无关内容")]
        assert [r.chunk_id for r in order_results_facet_first(pool, facets)] == [1, 2]
        assert order_results_facet_first([], facets) == []


# ---------------------------------------------------------------------------
# 2. 发送前硬上限适配
# ---------------------------------------------------------------------------


class TestFitExtractReductionRound:
    def test_round0_within_cap_renders_once(self):
        calls: list[int] = []

        def render(r: int) -> tuple[str, str]:
            calls.append(r)
            return "系统提示", "普通长度的提取 Prompt"

        assert fit_extract_reduction_round(render, max_rounds=3) == 0
        assert calls == [0]

    def test_shrinks_until_within_cap(self, monkeypatch):
        monkeypatch.setattr(settings, "REVIEW_PROMPT_HARD_CAP_TOKENS", 100)

        def render(r: int) -> tuple[str, str]:
            return "", "超" * (1000 if r == 0 else 50)

        assert fit_extract_reduction_round(render, max_rounds=3) == 1

    def test_all_rounds_over_cap_returns_none(self, monkeypatch):
        monkeypatch.setattr(settings, "REVIEW_PROMPT_HARD_CAP_TOKENS", 10)

        def render(r: int) -> tuple[str, str]:
            return "", "超" * 1000

        assert fit_extract_reduction_round(render, max_rounds=2) is None


# ---------------------------------------------------------------------------
# 3. _call_llm_extract 超限合同(不发请求、转提取失败待复核)
# ---------------------------------------------------------------------------


class _ExplodingLLM:
    """硬上限不可满足时 LLM 不得被调用。"""

    async def chat_json(self, **kwargs):
        raise AssertionError("prompt oversize 时禁止发起 LLM 请求")


class _StubLLM:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = 0

    async def chat_json(self, **kwargs):
        self.calls += 1
        return dict(self.payload)


def _make_extractor(llm) -> BidExtractor:
    return BidExtractor(db=None, llm_client=llm, max_retries=1, top_k=8)


async def test_oversize_returns_failed_signal_without_llm_call(monkeypatch):
    monkeypatch.setattr(settings, "REVIEW_PROMPT_HARD_CAP_TOKENS", 10)
    extractor = _make_extractor(_ExplodingLLM())

    result = await extractor._call_llm_extract(
        req_id=1,
        category=None,
        description="资质证明文件要求: 提供有效的营业执照及授权委托书" * 5,
        is_mandatory=True,
        search_results=[_sr(1, "营业执照及授权材料" * 50)],
        image_chunks=[],
    )

    assert isinstance(result, ExtractionFailedSignal)
    assert result.reason == OVERSIZE_REASON


async def test_normal_path_still_extracts(monkeypatch):
    """默认硬帽下正常提取路径不受影响(回归护栏)。"""
    stub = _StubLLM({
        "requirement_id": 1,
        "content": "投标人第7页提供了法定代表人授权委托书",
        "evidence_type": "certificate",
        "source_pages": [7],
        "found": True,
        "completeness": "full",
        "missing_elements": [],
    })
    extractor = _make_extractor(stub)

    result = await extractor._call_llm_extract(
        req_id=1,
        category=None,
        description="提供法定代表人授权委托书",
        is_mandatory=True,
        search_results=[_sr(1, "法定代表人授权委托书扫描件", page=7)],
        image_chunks=[],
    )

    assert isinstance(result, ExtractedBidResponse)
    assert result.found is True
    assert stub.calls == 1


# ---------------------------------------------------------------------------
# 4. 硬帽下沉后的导入兼容
# ---------------------------------------------------------------------------


def test_hard_cap_reexport_stays_importable():
    from app.core.ai.reviewer_parts.evidence_pack_budget import (
        estimate_prompt_tokens,
        prompt_hard_cap_violation,
    )

    assert prompt_hard_cap_violation is input_budget.prompt_hard_cap_violation
    assert estimate_prompt_tokens is input_budget.estimate_prompt_tokens
