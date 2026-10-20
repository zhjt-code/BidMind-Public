"""端到端冒烟 — 三组场景的链路级断言 (不依赖真实 LLM/DB)。

场景对应:
- P-empty: 零素材时降级聚合不误报, gate_flags 应显示"素材未绑定"类提示
- P-normal: 标准链路 → DegradationAggregator 无 severe_kinds, gate 可 pass
- P-stress: 50% 素材打标失败 → label_conflict 保留原值, _tag_status=llm_failed,
            降级聚合触发 severe, writing_compose_task 末尾 error_message 前缀告警。

使用 AsyncMock + FakeMaterial/FakeDraft 驱动聚合器与 material_tagger 的真实行为。
真正的 DB/LLM 冒烟应在 staging 手工执行, 见 docs/writer_e2e_smoke.md。
"""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test"
)

from app.core.observability.degradation_aggregator import (
    clear_aggregator,
    get_aggregator,
    set_aggregator_for_task,
    set_denominator,
)


@pytest.fixture(autouse=True)
def _cleanup_agg():
    yield
    clear_aggregator()


def _make_fake_material(mid: int, title: str = "素材"):
    mat = MagicMock()
    mat.id = mid
    mat.title = title
    mat.display_title = None
    mat.content = "素材正文内容"
    mat.material_type = "text"
    mat.metadata_tags = None
    mat.label_conflict = None
    mat.resolved_label = None
    mat.structure_hint_label = None
    mat.file_name_label = None
    return mat


class TestScenarioPEmpty:
    """P-empty: 零素材, 仅约束 + 行业画像。"""

    def test_no_materials_no_degradation(self):
        set_aggregator_for_task("writer_compose")
        set_denominator("material_tag_failed", 0)
        set_denominator("compose_failed", 10)
        # 零素材情况下不会有 material_tag_failed, 章节生成也未失败
        summary = get_aggregator().summarize()
        assert summary.severe_kinds == []
        assert summary.text == ""


class TestScenarioPNormal:
    """P-normal: 标准链路, 全部打标成功。"""

    @pytest.mark.asyncio
    async def test_tag_batch_no_failure(self):
        from app.core.ai.retry_tracker import LLMRetryTracker
        from app.core.writer.material_tagger import MaterialTagger

        llm = AsyncMock()
        llm.chat_json = AsyncMock(return_value={
            "domain": "项目业绩",
            "entities": [],
            "keywords": ["业绩"],
            "applicable_sections": ["项目经验"],
        })

        tagger = MaterialTagger.__new__(MaterialTagger)
        tagger.db = AsyncMock()
        tagger.db.commit = AsyncMock()
        tagger.llm_client = llm
        tagger._retry_tracker = LLMRetryTracker("t")

        set_aggregator_for_task("writer_compose")
        mats = [_make_fake_material(i) for i in range(1, 6)]
        await tagger.tag_batch(mats, auto_retry_failed=False)

        agg = get_aggregator()
        summary = agg.summarize()
        assert summary.counts_by_kind.get("material_tag_failed", 0) == 0
        # 每条应 _tag_status=llm_success
        for m in mats:
            data = json.loads(m.metadata_tags)
            assert data.get("_tag_status") == "llm_success"


class TestScenarioPStress:
    """P-stress: 人为注入 50% 素材打标失败。"""

    @pytest.mark.asyncio
    async def test_half_failure_triggers_severe_degradation(self):
        from app.core.ai.retry_tracker import LLMRetryTracker
        from app.core.writer.material_tagger import MaterialTagger

        call_idx = {"n": 0}

        async def flaky_chat_json(*a, **kw):
            call_idx["n"] += 1
            # 偶数次失败, 奇数次成功 → 恰好 50%
            if call_idx["n"] % 2 == 0:
                raise TimeoutError("simulated provider timeout")
            return {
                "domain": "其他", "entities": [], "keywords": [],
                "applicable_sections": [],
            }

        llm = AsyncMock()
        llm.chat_json = AsyncMock(side_effect=flaky_chat_json)

        tagger = MaterialTagger.__new__(MaterialTagger)
        tagger.db = AsyncMock()
        tagger.db.commit = AsyncMock()
        tagger.llm_client = llm
        tagger._retry_tracker = LLMRetryTracker("stress")

        set_aggregator_for_task("writer_compose")
        mats = [_make_fake_material(i) for i in range(1, 11)]  # 10 份
        set_denominator("material_tag_failed", len(mats))

        await tagger.tag_batch(mats, auto_retry_failed=False)

        # 50% 失败
        failed = [
            m for m in mats
            if m.metadata_tags
            and json.loads(m.metadata_tags).get("_tag_status") == "llm_failed"
        ]
        assert len(failed) == 5

        # 降级聚合应判定为严重 (5/10 = 50% > 20% 阈值)
        summary = get_aggregator().summarize()
        assert "material_tag_failed" in summary.severe_kinds
        assert "素材打标失败 5/10" in summary.text

    @pytest.mark.asyncio
    async def test_tag_failed_material_still_recallable_via_resolved_label(self):
        """打标失败素材不致完全失去匹配能力(迁移自旧 tag_failed 放宽向量阈值策略)。

 旧机制: matcher 对 _tag_status=llm_failed 的素材放宽 chunk 向量兜底阈值。
 新机制: 五路瀑布已删除; tag 失败素材没有 applicable_sections,
 但只要有 resolved_label(标签决议真源)即可经字面召回进入候选, 由 LLM 句对
 裁决决定归属; 语义召回(safe_encode)另行兜底。此处断言确定性字面路径。
 """
        from app.core.writer.material_matching.candidate_recall import recall_candidates
        from app.core.writer.material_matching.contracts import ChapterDemandProfile
        from app.core.writer.material_matching.profiles import build_material_profiles

        failed_mat = _make_fake_material(2, title="公司简介")
        failed_mat.resolved_label = "公司简介"
        failed_mat.label_conflict = "matched"
        failed_mat.metadata_tags = json.dumps({"_tag_status": "llm_failed"})

        profiles = build_material_profiles([failed_mat])
        chapters = [ChapterDemandProfile(node_id=90, title="公司简介")]

        candidates = await recall_candidates(profiles, chapters, encoder=None)

        assert any(
            c.material_id == 2 and c.node_id == 90 for c in candidates
        ), "tag 失败素材应仍可经 resolved_label 字面召回进入候选"
