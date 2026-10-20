"""B1 防御测试 — FindingPageQuoteVerifyGate 联合校验。

覆盖关键反例：
- 用户图 4 场景：page=30 但 quote 实际在第 31 页 → verified=False（拒绝放行）
- 用户图 4 场景：page=45 但 quote='供商'（短引文）不在该页 → verified=False
- page 与 quote 同时给出且联合命中 → verified=True
- 仅 page 没 quote → 警告但不拒绝
- 仅 quote 没 page，能匹配某 chunk → 回填 page 并通过
"""

from __future__ import annotations

from app.core.ai.gates.base import GateContext
from app.core.ai.gates.collusion.finding_page_quote_verify import (
    FindingPageQuoteVerifyGate,
)


def _build_evidence(
    *,
    doc_a_id: int = 1,
    doc_b_id: int = 2,
    chunks_a: list[tuple[int, str]] | None = None,
    chunks_b: list[tuple[int, str]] | None = None,
) -> dict:
    chunks_a = chunks_a or []
    chunks_b = chunks_b or []
    page_sets = {
        doc_a_id: {p for p, _ in chunks_a if p is not None},
        doc_b_id: {p for p, _ in chunks_b if p is not None},
    }
    chunk_texts = {doc_a_id: chunks_a, doc_b_id: chunks_b}
    page_to_contents: dict[int, dict[int, list[str]]] = {
        doc_a_id: {}, doc_b_id: {},
    }
    for did, items in chunk_texts.items():
        for pn, content in items:
            if pn is not None and content:
                page_to_contents[did].setdefault(int(pn), []).append(content)
    return {
        "page_sets": page_sets,
        "chunk_texts": chunk_texts,
        "page_to_contents": page_to_contents,
        "doc_a_id": doc_a_id,
        "doc_b_id": doc_b_id,
    }


def _ctx(target: dict, evidence: dict) -> GateContext:
    return GateContext(
        flow="collusion", stage="post_llm",
        target=target, evidence=evidence,
    )


class TestPageQuoteLocality:
    gate = FindingPageQuoteVerifyGate()

    def test_page_and_quote_match_same_chunk_passes(self):
        """长 quote 与 page 都给出，能在该 page 的 chunk 内找到 -> 通过。"""
        evidence = _build_evidence(
            chunks_a=[(30, "本项目钢材配送方案符合规范要求详见下表所示具体内容...")],
            chunks_b=[(30, "本项目钢材配送方案符合规范要求详见下表所示具体内容...")],
        )
        target = {
            "d3_text": {
                "score": 0.9,
                "findings": [{
                    "summary": "技术方案雷同",
                    "quote_a": "本项目钢材配送方案符合规范要求详见下表所示具体内容",
                    "page_a": "第30页",
                    "quote_b": "本项目钢材配送方案符合规范要求详见下表所示具体内容",
                    "page_b": "第30页",
                }],
            }
        }
        verdict = self.gate.evaluate(_ctx(target, evidence))
        f = target["d3_text"]["findings"][0]
        assert f.get("verified") is not False, f"finding 应通过，实际: {f}"
        # 不应是 enforce_patch（无修改/无拒绝）或仅 modified 计数
        assert verdict.verdict in {"pass", "enforce_patch"}

    def test_quote_in_wrong_page_rejected(self):
        """图 4 场景: page=30 但 quote 实际在第 31 页 → 拒绝。"""
        evidence = _build_evidence(
            chunks_a=[
                (30, "本页是封面无相关内容"),
                (31, "本项目钢材配送方案符合规范要求详见下表所示具体内容"),
            ],
            chunks_b=[
                (30, "本页是封面无相关内容"),
                (31, "本项目钢材配送方案符合规范要求详见下表所示具体内容"),
            ],
        )
        target = {
            "d3_text": {
                "score": 0.9,
                "findings": [{
                    "summary": "技术方案雷同",
                    "quote_a": "本项目钢材配送方案符合规范要求详见下表所示具体内容",
                    "page_a": "第30页",  # 实际 quote 在第 31 页
                    "quote_b": "本项目钢材配送方案符合规范要求详见下表所示具体内容",
                    "page_b": "第30页",
                }],
            }
        }
        self.gate.evaluate(_ctx(target, evidence))
        f = target["d3_text"]["findings"][0]
        assert f.get("verified") is False, "page 与 quote 不在同一 chunk，应拒绝"
        assert "rejection_reason" in f

    def test_short_quote_in_claimed_page_passes(self):
        """短引文 (<5 字) 必须在声称 page 的 chunk 内精确出现。"""
        evidence = _build_evidence(
            chunks_a=[(45, "钢材供应商资格证明附后")],
            chunks_b=[(45, "钢材供应商资格证明附后")],
        )
        target = {
            "d2_company": {
                "score": 0.6,
                "findings": [{
                    "summary": "供应商关联",
                    "quote_a": "供应商",
                    "page_a": "第45页",
                    "quote_b": "供应商",
                    "page_b": "第45页",
                }],
            }
        }
        self.gate.evaluate(_ctx(target, evidence))
        f = target["d2_company"]["findings"][0]
        assert f.get("verified") is not False

    def test_short_quote_not_in_claimed_page_rejected(self):
        """图 4 场景: 短 quote='供商' 不在 page=45 的 chunk → 拒绝。"""
        evidence = _build_evidence(
            chunks_a=[
                (45, "钢材采购验收记录附后"),  # 第 45 页无 "供商" 字符
                (60, "我方供商网络覆盖全国"),
            ],
            chunks_b=[
                (45, "钢材采购验收记录附后"),
                (60, "我方供商网络覆盖全国"),
            ],
        )
        target = {
            "d2_company": {
                "score": 0.6,
                "findings": [{
                    "summary": "供应商关联",
                    "quote_a": "供商",
                    "page_a": "第45页",
                    "quote_b": "供商",
                    "page_b": "第45页",
                }],
            }
        }
        self.gate.evaluate(_ctx(target, evidence))
        f = target["d2_company"]["findings"][0]
        assert f.get("verified") is False
        assert "未精确出现" in f.get("rejection_reason", "")

    def test_page_only_no_quote_warns_not_rejects(self):
        """仅 page 没 quote → 通过但加 warning。"""
        evidence = _build_evidence(
            chunks_a=[(20, "技术方案章节内容")],
            chunks_b=[(20, "技术方案章节内容")],
        )
        target = {
            "d6_format": {
                "score": 0.4,
                "findings": [{
                    "summary": "格式雷同",
                    "page_a": "第20页",
                    "page_b": "第20页",
                }],
            }
        }
        self.gate.evaluate(_ctx(target, evidence))
        f = target["d6_format"]["findings"][0]
        assert f.get("verified") is not False
        assert f.get("verification_warning")

    def test_quote_only_no_page_recovers_page(self):
        """仅 quote 没 page，且能在文档某 chunk 找到 → 回填 page 并通过。"""
        evidence = _build_evidence(
            chunks_a=[(25, "项目实施方案具体描述如下采用了先进技术架构和管理流程")],
            chunks_b=[(28, "项目实施方案具体描述如下采用了先进技术架构和管理流程")],
        )
        target = {
            "d3_text": {
                "score": 0.7,
                "findings": [{
                    "summary": "方案雷同",
                    "quote_a": "项目实施方案具体描述如下采用了先进技术架构和管理流程",
                    "quote_b": "项目实施方案具体描述如下采用了先进技术架构和管理流程",
                }],
            }
        }
        self.gate.evaluate(_ctx(target, evidence))
        f = target["d3_text"]["findings"][0]
        # 至少一侧成功回填
        assert f.get("page_a") or f.get("page_b")

    def test_page_outside_doc_pages_rejected(self):
        """page 给出但不在文档任一 chunk → 拒绝。"""
        evidence = _build_evidence(
            chunks_a=[(10, "第10页内容"), (11, "第11页内容")],
            chunks_b=[(10, "第10页内容"), (11, "第11页内容")],
        )
        target = {
            "d3_text": {
                "score": 0.5,
                "findings": [{
                    "summary": "...",
                    "page_a": "第999页",
                    "page_b": "第999页",
                    "quote_a": "本项目实施方案具体描述如下采用了先进技术架构",
                    "quote_b": "本项目实施方案具体描述如下采用了先进技术架构",
                }],
            }
        }
        self.gate.evaluate(_ctx(target, evidence))
        f = target["d3_text"]["findings"][0]
        assert f.get("verified") is False

    def test_no_locality_declared_passes(self):
        """finding 没声明 page 也没 quote → 不参与校验，通过。"""
        evidence = _build_evidence(
            chunks_a=[(1, "any")],
            chunks_b=[(1, "any")],
        )
        target = {
            "d3_text": {
                "score": 0.3,
                "findings": [{"summary": "纯描述类发现"}],
            }
        }
        self.gate.evaluate(_ctx(target, evidence))
        f = target["d3_text"]["findings"][0]
        assert f.get("verified") is not False
