"""C3 防御测试 — D7ConcreteEvidenceGate 具体证据校验。

覆盖用户图 3 反例（最严重）：
- D7 错别字 finding 的 quote_a == quote_b（自相矛盾，"应为'钢材配送方案'"）
  → verified=False，rejection_reason 中含 "完全相同"
- D7 错别字 finding 无 quote → verified=False
- D7 排版错误 finding detail 中无具体位置/编号 → verified=False
- D6 finding detail 中无具体格式特征关键词 → verified=False
- 合规 finding 不被误伤
"""

from __future__ import annotations

import pytest

from app.core.ai.gates.base import GateContext
from app.core.ai.gates.collusion.d7_concrete_evidence import D7ConcreteEvidenceGate


def _ctx(target: dict) -> GateContext:
    return GateContext(flow="collusion", stage="post_llm", target=target, evidence={})


class TestD7TypoFindings:
    gate = D7ConcreteEvidenceGate()

    def test_typo_quote_self_contradicts_rejected(self):
        """图 3 反例：声称错别字但 quote_a == quote_b，自相矛盾。"""
        target = {
            "d7_abnormal": {
                "score": 0.5,
                "findings": [{
                    "summary": "存在相同非标准表述",
                    "detail": "两份文件在第30页均出现相同的非标准表述'钢材配送方案'（应为'钢材配送方案'），且排版错误一致。",
                    "quote_a": "钢材配送方案",
                    "quote_b": "钢材配送方案",
                }],
            }
        }
        v = self.gate.evaluate(_ctx(target))
        assert v.verdict == "enforce_patch"
        f = target["d7_abnormal"]["findings"][0]
        assert f.get("verified") is False
        assert "完全相同" in f.get("rejection_reason", "")

    def test_typo_no_quote_rejected(self):
        target = {
            "d7_abnormal": {
                "score": 0.5,
                "findings": [{
                    "summary": "存在相同错别字",
                    "detail": "两份文件中均出现相同的非标准术语或错别字",
                    # 无 quote_a / quote_b
                }],
            }
        }
        self.gate.evaluate(_ctx(target))
        f = target["d7_abnormal"]["findings"][0]
        assert f.get("verified") is False
        assert "未提供" in f.get("rejection_reason", "")

    def test_typo_with_real_diff_passes(self):
        """合规错别字 finding：quote_a 与 quote_b 有真实差异。"""
        target = {
            "d7_abnormal": {
                "score": 0.5,
                "findings": [{
                    "summary": "存在相同错别字",
                    "detail": "两份文件均将'防腐'写作'防府'",
                    "quote_a": "防府保温施工",
                    "quote_b": "防府保温施工方案",
                }],
            }
        }
        self.gate.evaluate(_ctx(target))
        f = target["d7_abnormal"]["findings"][0]
        # quote_a 与 quote_b 不同（一个有"方案"一个没有），应放行
        assert f.get("verified") is not False


class TestD7LayoutFindings:
    gate = D7ConcreteEvidenceGate()

    def test_layout_no_location_rejected(self):
        target = {
            "d7_abnormal": {
                "score": 0.4,
                "findings": [{
                    "summary": "存在相同排版错误",
                    "detail": "两份文件均出现页码跳跃和图表编号错乱",
                    # 没有具体位置/编号
                }],
            }
        }
        self.gate.evaluate(_ctx(target))
        f = target["d7_abnormal"]["findings"][0]
        assert f.get("verified") is False

    def test_layout_with_location_passes(self):
        target = {
            "d7_abnormal": {
                "score": 0.4,
                "findings": [{
                    "summary": "排版错误",
                    "detail": "第 30 页页码跳跃，图 3-2 编号错位",
                }],
            }
        }
        self.gate.evaluate(_ctx(target))
        f = target["d7_abnormal"]["findings"][0]
        assert f.get("verified") is not False


class TestD6Findings:
    gate = D7ConcreteEvidenceGate()

    def test_d6_no_concrete_feature_rejected(self):
        target = {
            "d6_format": {
                "score": 0.3,
                "findings": [{
                    "summary": "格式雷同",
                    "detail": "两份文件格式高度一致",
                }],
            }
        }
        self.gate.evaluate(_ctx(target))
        f = target["d6_format"]["findings"][0]
        assert f.get("verified") is False

    def test_d6_concrete_feature_passes(self):
        target = {
            "d6_format": {
                "score": 0.3,
                "findings": [{
                    "summary": "目录编号一致",
                    "detail": "两份文件目录二级章节均采用罗马数字编号且字体使用宋体",
                }],
            }
        }
        self.gate.evaluate(_ctx(target))
        f = target["d6_format"]["findings"][0]
        assert f.get("verified") is not False


class TestGateMisc:
    gate = D7ConcreteEvidenceGate()

    def test_already_rejected_finding_skipped(self):
        target = {
            "d7_abnormal": {
                "findings": [{
                    "summary": "前置 Gate 已拒绝",
                    "verified": False,
                    "rejection_reason": "原 reason",
                }],
            }
        }
        self.gate.evaluate(_ctx(target))
        f = target["d7_abnormal"]["findings"][0]
        # 不应覆盖已有 rejection_reason
        assert f["rejection_reason"] == "原 reason"

    def test_no_d6_d7_passes(self):
        target = {"d1_personnel": {"score": 0.5, "findings": [{"summary": "无关"}]}}
        v = self.gate.evaluate(_ctx(target))
        assert v.verdict == "pass"

    def test_applies_only_collusion_post_llm(self):
        ctx_other = GateContext(flow="investigate", stage="post_llm", target={}, evidence={})
        assert self.gate.applies(ctx_other) is False
        ctx_pre = GateContext(flow="collusion", stage="pre_llm", target={}, evidence={})
        assert self.gate.applies(ctx_pre) is False
        ctx_ok = GateContext(flow="collusion", stage="post_llm", target={}, evidence={})
        assert self.gate.applies(ctx_ok) is True
