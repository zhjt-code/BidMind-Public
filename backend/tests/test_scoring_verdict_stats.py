"""评分 verdict 探针构建与决策面禁入。

职责: 锁定探针构建口径、审查落库接线，并禁止已退役的链路裁决聚合回流。
不做: 不访问 DB、不调 LLM、不做三分类/裁决表聚合。
"""
from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.extractor.scoring.extraction_observability import build_verdict_probe

pytestmark = pytest.mark.scoring


class TestProbeBuild:
    def test_probe_captures_verdict_adoption_and_rounds(self):
        tail = SimpleNamespace(
            adopted=True,
            report=SimpleNamespace(needs_review=False),
            l3_diagnostics=[{"channel": "vlm_rescue", "rounds": 2, "status": "ok"}],
        )
        probe = build_verdict_probe(trigger_verdict="amber", tail=tail)
        assert probe["trigger_verdict"] == "amber"
        assert probe["rescue_adopted"] is True
        assert probe["rescue_rounds"] == 2
        assert probe["rescue_status"] == "ok"
        assert probe["final_needs_review"] is False
        assert probe["at"]

    def test_probe_without_vlm_meta_defaults(self):
        tail = SimpleNamespace(
            adopted=False, report=SimpleNamespace(needs_review=False), l3_diagnostics=[]
        )
        probe = build_verdict_probe(trigger_verdict="green", tail=tail)
        assert probe["rescue_rounds"] == 0
        assert probe["rescue_status"] == ""
        assert probe["final_verdict"] == "green"  # 无救援=终态即触发态

    def test_probe_keeps_pdf_adoption_when_vlm_rejected(self):
        """: PDF 已采纳后 VLM rejected 不得盖掉 rescue_status。"""
        tail = SimpleNamespace(
            adopted=True,
            report=SimpleNamespace(needs_review=True),
            l3_diagnostics=[
                {
                    "channel": "pdf_chat_rescue", "rounds": 2,
                    "status": "ok_needs_review", "final_verdict": "amber",
                },
                {
                    "channel": "vlm_rescue", "rounds": 3,
                    "status": "rejected", "final_verdict": "amber",
                },
            ],
        )
        probe = build_verdict_probe(trigger_verdict="amber", tail=tail)
        assert probe["pdf_rescue_rounds"] == 2
        assert probe["pdf_rescue_status"] == "ok_needs_review"
        assert probe["vlm_rescue_rounds"] == 3
        assert probe["vlm_rescue_status"] == "rejected"
        assert probe["rescue_status"] == "ok_needs_review"
        assert probe["rescue_rounds"] == 2
        assert probe["rescue_adopted"] is True

    def test_probe_records_final_verdict_from_vlm_meta(self):
        """ doc2484: 采纳后闭合报告 needs_review=False 但终verdict仍amber,
 探针必须记录复判结果而非被闭合口径冒充。"""
        tail = SimpleNamespace(
            adopted=True,
            report=SimpleNamespace(needs_review=False),
            l3_diagnostics=[{
                "channel": "vlm_rescue", "rounds": 3,
                "status": "ok", "final_verdict": "amber",
            }],
        )
        probe = build_verdict_probe(trigger_verdict="amber", tail=tail)
        assert probe["final_verdict"] == "amber"
        assert probe["final_needs_review"] is False  # 两口径并存, 可观测偏差


class TestPersistWiring:
    def test_review_extract_stamps_probe_via_shared_store(self):
        """: 审查落库与写标共用 stamp_document_verdict_probe, 并标记 flow=review。"""
        from app.core.extractor.scoring.scoring_extractor import ScoringExtractor

        source = inspect.getsource(ScoringExtractor.extract_from_document)
        assert "stamp_document_verdict_probe" in source
        assert 'flow="review"' in source


class TestDecisionSurfaceStaysDeleted:
    """同形态第二例: 退役的链路裁决聚合不得从 service 或 admin 路由回流。"""

    def test_stats_service_file_stays_deleted(self):
        service = (
            Path(__file__).parent.parent
            / "app"
            / "services"
            / "scoring_verdict_stats_service.py"
        )
        assert not service.exists(), (
            "评分链路裁决聚合服务已随决策面删除，不得复活"
        )

    def test_admin_ops_route_stays_deleted(self):
        from app.api.routers import admin_ops

        source = inspect.getsource(admin_ops)
        assert "scoring-verdict-stats" not in source
        assert "get_scoring_verdict_stats" not in source
        assert "build_scoring_verdict_stats" not in source
