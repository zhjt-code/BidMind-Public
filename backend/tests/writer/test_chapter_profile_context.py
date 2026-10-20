"""P2.3 防御测试：chapter_profile prompt 注入 industry_profile_excerpt
+ clause_domain_summary, 保持向后兼容。"""

from __future__ import annotations

import os

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test"
)

from app.core.writer.prompts.chapter_profile import (
    CHAPTER_PROFILE_USER,
    get_chapter_profile_prompt,
)


class TestChapterProfilePrompt:

    def test_user_template_contains_new_placeholders(self):
        """模板必须包含四个占位符 (tree_text / leaf_list / industry_profile_excerpt / clause_domain_summary)。"""
        assert "{tree_text}" in CHAPTER_PROFILE_USER
        assert "{leaf_list}" in CHAPTER_PROFILE_USER
        assert "{industry_profile_excerpt}" in CHAPTER_PROFILE_USER
        assert "{clause_domain_summary}" in CHAPTER_PROFILE_USER

    def test_backward_compatible_signature(self):
        """不传新参数也能 format, 占位符显示 (无...)。"""
        _system, user = get_chapter_profile_prompt(
            tree_text="- 一\n- 二",
            leaf_list="- ID=1, 标题=投标函",
        )
        assert "(无行业画像)" in user
        assert "(无约束分布摘要)" in user

    def test_new_params_injected(self):
        _system, user = get_chapter_profile_prompt(
            tree_text="- 一",
            leaf_list="- ID=1",
            industry_profile_excerpt="食品供应行业画像: 强调冷链与资质",
            clause_domain_summary="scoring_criterion=8, hard_constraint=3",
        )
        assert "食品供应行业画像" in user
        assert "scoring_criterion=8" in user
