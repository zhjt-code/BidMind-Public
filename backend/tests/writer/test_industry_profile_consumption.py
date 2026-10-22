""" 防御测试：行业画像在下游节点的实际消费。

风险映射：
-: 章节画像与自由论述用 ``content[:N]`` 头部截断，生产 20 个项目中
 仅 2 个能拿到"评标专家核心关注点"，差异化竞争建议亦被尾部截断
-: planner 消化画像后产出的 ``industry_anchors`` 未被渲染进正文
 生成 Prompt，等于该次 LLM 消化对正文完全无效
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test"
)

from app.core.writer.composer.free_writing_route import build_context_within_budget
from app.core.writer.outline_generator import _CHAPTER_PROFILE_INDUSTRY_CHARS
from app.core.writer.section_plan.service import render_section_plan_for_prompt


def _section(heading: str, total_chars: int, marker: str) -> str:
    body = f"\n{marker}"
    padding = max(total_chars - len(heading) - len(body), 0)
    return heading + body + ("论述填充内容" * (padding // 6 + 1))[:padding]


@pytest.fixture
def realistic_profile() -> str:
    """复刻生产库画像的段落长度分布，评标关注点起始偏移超过 1500。"""
    return "\n\n".join(
        [
            _section("### 1. 行业识别与典型特征", 600, "行业特征标记"),
            _section("### 2. 专业术语表（20-30个）", 370, "术语表标记"),
            _section("### 3. 技术方案典型结构", 1300, "技术结构标记"),
            _section("### 4. 评标专家核心关注点（5-8个）", 440, "评标关注点标记"),
            _section("### 5. 写作风格指导", 400, "写作风格标记"),
            _section("### 6. 差异化竞争建议", 470, "差异化建议标记"),
        ]
    )


class TestFreeWritingContextConsumesProfile:

    @staticmethod
    def _fake_composer() -> SimpleNamespace:
        return SimpleNamespace(
            _format_chapter_profile_text=lambda profile: "",
            _build_material_context_entries=lambda *a, **kw: [],
            _format_image_metadata=lambda images: "",
        )

    def test_reviewer_focus_and_differentiation_reach_prompt(
        self, realistic_profile: str
    ):
        context = build_context_within_budget(
            self._fake_composer(),
            industry_profile=realistic_profile,
            clauses=[],
            materials=[],
            previous_summaries=[],
        )
        excerpt = str(context["profile_excerpt"])
        assert "评标关注点标记" in excerpt
        assert "差异化建议标记" in excerpt
        assert "写作风格标记" in excerpt

    def test_head_slice_would_have_lost_differentiation(
        self, realistic_profile: str
    ):
        """锁定原缺陷形态：3000 字符头部截断拿不全差异化建议。"""
        assert len(realistic_profile) > 3000
        assert "差异化建议标记" not in realistic_profile[:3000]

    def test_missing_profile_yields_empty_excerpt(self):
        context = build_context_within_budget(
            self._fake_composer(),
            industry_profile=None,
            clauses=[],
            materials=[],
            previous_summaries=[],
        )
        assert context["profile_excerpt"] == ""

    def test_unparseable_profile_falls_back_to_head_slice(self):
        fallback_text = "（行业画像生成失败，请手动补充）"
        context = build_context_within_budget(
            self._fake_composer(),
            industry_profile=fallback_text,
            clauses=[],
            materials=[],
            previous_summaries=[],
        )
        assert context["profile_excerpt"] == fallback_text


class TestChapterProfileConsumesProfile:

    @staticmethod
    def _leaf_node() -> SimpleNamespace:
        return SimpleNamespace(
            id=10,
            parent_id=None,
            depth=0,
            sort_order=0,
            title="技术方案",
            node_type=SimpleNamespace(value="free_writing"),
            volume_type="technical",
            chapter_profile=None,
        )

    @pytest.mark.asyncio
    async def test_reviewer_focus_reaches_chapter_profile_prompt(
        self, realistic_profile: str, monkeypatch: pytest.MonkeyPatch
    ):
        """ 核心断言：1500 预算下评标关注点必须进入章节画像 Prompt。"""
        from app.core.writer import outline_generator as module

        node = self._leaf_node()
        captured: dict[str, str] = {}

        def _fake_prompt(tree_text, leaf_list, **kwargs):
            captured["excerpt"] = kwargs.get("industry_profile_excerpt", "")
            return "sys", "user"

        monkeypatch.setattr(
            "app.core.writer.prompts.chapter_profile.get_chapter_profile_prompt",
            _fake_prompt,
        )

        generator = module.OutlineGenerator.__new__(module.OutlineGenerator)
        generator.db = SimpleNamespace(
            execute=AsyncMock(
                side_effect=[
                    SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [node])),
                    SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [])),
                ]
            ),
            commit=AsyncMock(),
        )
        generator._load_clause = AsyncMock(
            return_value=SimpleNamespace(content=realistic_profile)
        )
        generator._chat_with_input_guard = AsyncMock(
            return_value=json.dumps(
                [
                    {
                        "node_id": 10,
                        "semantic_role": "技术方案主体",
                        "expects": ["评分标准"],
                        "rejects": ["报价明细"],
                    }
                ],
                ensure_ascii=False,
            )
        )

        await generator.generate_chapter_profiles(project_id=553)

        assert "评标关注点标记" in captured["excerpt"]
        assert len(captured["excerpt"]) <= _CHAPTER_PROFILE_INDUSTRY_CHARS

    def test_budget_constant_matches_original_contract(self):
        """预算沿用原值，本次只改选段策略不放大 token 占用。"""
        assert _CHAPTER_PROFILE_INDUSTRY_CHARS == 1500


class TestSectionPlanAnchorsRendered:

    def test_industry_anchors_rendered_into_prompt(self):
        """: planner 消化画像的产物必须回渲到正文 Prompt。"""
        plan = {
            "section_goal": "阐述腐蚀监测方案",
            "arguments": [{"claim": "分区监测降低漏检"}],
            "measures": [{"action": "布设监测点", "verification": "月度巡检记录"}],
            "acceptance_criteria": [
                {"criterion": "监测覆盖率", "verification_method": "台账核对"}
            ],
            "industry_anchors": [
                {"anchor_id": "anchor_1", "guidance": "使用多相流与缓蚀剂等行业表述"},
                {"anchor_id": "anchor_2", "guidance": "引用电化学工作站测试口径"},
            ],
        }
        rendered = render_section_plan_for_prompt(plan)
        assert "使用多相流与缓蚀剂等行业表述" in rendered
        assert "引用电化学工作站测试口径" in rendered
        assert "行业表达要点" in rendered

    def test_existing_plan_fields_still_rendered(self):
        plan = {
            "section_goal": "目标",
            "arguments": [{"claim": "论点A"}],
            "measures": [{"action": "措施A", "verification": "验证A"}],
            "acceptance_criteria": [
                {"criterion": "验收A", "verification_method": "方法A"}
            ],
            "industry_anchors": [{"guidance": "要点A"}],
        }
        rendered = render_section_plan_for_prompt(plan)
        for expected in ["目标", "论点A", "措施A", "验收A", "要点A"]:
            assert expected in rendered

    def test_plan_without_anchors_is_unchanged(self):
        plan = {
            "section_goal": "目标",
            "arguments": [{"claim": "论点A"}],
            "measures": [],
            "acceptance_criteria": [],
        }
        rendered = render_section_plan_for_prompt(plan)
        assert "行业表达要点" not in rendered
        assert rendered.endswith("论点A")

    def test_blank_guidance_entries_skipped(self):
        """空 guidance 不得产出空标签行，否则 Prompt 出现无意义噪声。"""
        plan = {
            "section_goal": "目标",
            "industry_anchors": [
                {"guidance": "   "},
                {"anchor_id": "anchor_2"},
                {"guidance": "有效要点"},
            ],
        }
        rendered = render_section_plan_for_prompt(plan)
        assert rendered.count("行业表达要点") == 1
        assert "有效要点" in rendered

    def test_anchor_count_capped(self):
        plan = {
            "section_goal": "目标",
            "industry_anchors": [
                {"guidance": f"要点{index}"} for index in range(1, 12)
            ],
        }
        rendered = render_section_plan_for_prompt(plan)
        assert rendered.count("行业表达要点") == 6

    def test_total_length_still_capped(self):
        plan = {
            "section_goal": "目标" * 100,
            "arguments": [{"claim": "论点" * 200} for _ in range(8)],
            "industry_anchors": [{"guidance": "要点" * 300} for _ in range(6)],
        }
        assert len(render_section_plan_for_prompt(plan)) <= 3000

    def test_none_plan_keeps_degraded_message(self):
        assert "不可用" in render_section_plan_for_prompt(None)
