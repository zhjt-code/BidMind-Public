"""回归测试: PromptRegistry 命中时模板变量必须被注入真实内容

根因: _registry_prompt_wrapper / get_industry_profile_prompt 命中 Registry 后
直接返回原始 user_template，未对 {content}/{project_context} 等占位符做格式化，
导致 LLM 收到的是空模板而非真实招标文本，输出纯幻觉。

覆盖场景:
1. clause_extractor: Registry 模板含 {content}/{page_start}/{page_end} 时正确注入
2. clause_extractor: Registry 模板变量缺失时回退代码默认 Prompt
3. industry_profiler: Registry 模板含 {project_context} 等变量时正确注入
4. industry_profiler: Registry 模板变量缺失时回退代码默认 Prompt
"""

from dataclasses import dataclass
from typing import Optional
from unittest.mock import patch


@dataclass
class _FakeResolved:
    node_key: str
    system_message: str = ""
    user_template: str = ""
    temperature: float = 0.1
    max_tokens: int = 2000
    system_version_id: Optional[int] = None
    user_version_id: Optional[int] = None


class _FakeRegistry:
    def __init__(self, resolved: _FakeResolved):
        self._resolved = resolved

    def get_prompt(self, node_key: str) -> _FakeResolved:
        return self._resolved


# ────────────────────────────────────────────────
# clause_extractor: _registry_prompt_wrapper
# ────────────────────────────────────────────────

class TestClauseExtractorRegistryWrapper:
    """_registry_prompt_wrapper 命中 Registry 时必须格式化 user_template。"""

    def _build_wrapper(self, registry: _FakeRegistry):
        """构造一个绑定到给定 registry 的 wrapped 函数。"""
        from app.core.writer.clause_extractor import _registry_prompt_wrapper

        def _code_fallback(content, page_start, page_end):
            return "FALLBACK_SYS", f"FALLBACK_USER:{content}:{page_start}:{page_end}"

        wrapper = _registry_prompt_wrapper("writing.hard_clause", _code_fallback)
        return wrapper, registry

    def test_registry_hit_injects_variables(self):
        """命中 Registry 且模板含占位符时，真实变量必须被注入。"""
        resolved = _FakeResolved(
            node_key="writing.hard_clause",
            system_message="SYS_FROM_REGISTRY",
            user_template="招标内容:{content} 页码:{page_start}-{page_end}",
        )
        registry = _FakeRegistry(resolved)

        with patch(
            "app.core.ai.prompt_registry.get_registry",
            return_value=registry,
        ):
            from app.core.writer.clause_extractor import _registry_prompt_wrapper

            def _code_fallback(content, page_start, page_end):
                return "FALLBACK_SYS", "FALLBACK_USER"

            wrapper = _registry_prompt_wrapper("writing.hard_clause", _code_fallback)
            sys_msg, user_msg = wrapper("塔里木盆地油藏开发", 1, 5)

        assert sys_msg == "SYS_FROM_REGISTRY"
        assert "塔里木盆地油藏开发" in user_msg
        assert "1" in user_msg and "5" in user_msg
        assert "{content}" not in user_msg
        assert "{page_start}" not in user_msg

    def test_registry_hit_bad_template_falls_back(self):
        """模板含 Registry 不认识的变量时，应回退到代码默认 Prompt。"""
        resolved = _FakeResolved(
            node_key="writing.hard_clause",
            system_message="SYS_FROM_REGISTRY",
            user_template="需要 {content} 但也需要 {unknown_var}",
        )
        registry = _FakeRegistry(resolved)

        with patch(
            "app.core.ai.prompt_registry.get_registry",
            return_value=registry,
        ):
            from app.core.writer.clause_extractor import _registry_prompt_wrapper

            def _code_fallback(content, page_start, page_end):
                return "FALLBACK_SYS", f"FALLBACK_USER:{content}"

            wrapper = _registry_prompt_wrapper("writing.hard_clause", _code_fallback)
            sys_msg, user_msg = wrapper("测试内容", 1, 3)

        assert sys_msg == "FALLBACK_SYS"
        assert "FALLBACK_USER:测试内容" == user_msg

    def test_registry_miss_uses_code_default(self):
        """Registry 未命中（空模板）时，使用代码默认 Prompt。"""
        resolved = _FakeResolved(
            node_key="writing.hard_clause",
            system_message="",
            user_template="",
        )
        registry = _FakeRegistry(resolved)

        with patch(
            "app.core.ai.prompt_registry.get_registry",
            return_value=registry,
        ):
            from app.core.writer.clause_extractor import _registry_prompt_wrapper

            def _code_fallback(content, page_start, page_end):
                return "CODE_SYS", f"CODE_USER:{content}"

            wrapper = _registry_prompt_wrapper("writing.hard_clause", _code_fallback)
            sys_msg, user_msg = wrapper("默认路径测试", 2, 4)

        assert sys_msg == "CODE_SYS"
        assert "默认路径测试" in user_msg


# ────────────────────────────────────────────────
# industry_profiler: get_industry_profile_prompt
# ────────────────────────────────────────────────

class TestIndustryProfilerRegistryWrapper:
    """get_industry_profile_prompt 命中 Registry 时必须格式化 user_template。"""

    def test_registry_hit_injects_context(self):
        """命中 Registry 且模板含业务占位符时，上下文变量必须被注入。"""
        resolved = _FakeResolved(
            node_key="writing.industry_profile",
            system_message="PROFILE_SYS",
            user_template=(
                "背景:{project_context}\n"
                "评分:{scoring_summary}\n"
                "内容:{content_requirements}\n"
                "材料:{material_requirements}"
            ),
        )
        registry = _FakeRegistry(resolved)

        with patch(
            "app.core.writer.industry_profiler._get_reg",
            return_value=registry,
        ):
            from app.core.writer.industry_profiler import get_industry_profile_prompt

            sys_msg, user_msg = get_industry_profile_prompt(
                project_context="石油勘探开发",
                scoring_summary="技术方案20分",
                content_requirements="数值模拟方案",
                material_requirements="PVT实验数据",
            )

        assert sys_msg == "PROFILE_SYS"
        assert "石油勘探开发" in user_msg
        assert "技术方案20分" in user_msg
        assert "数值模拟方案" in user_msg
        assert "PVT实验数据" in user_msg
        assert "{project_context}" not in user_msg
        assert "industry_profile_output_contract:v1" in user_msg

    def test_registry_hit_bad_template_falls_back(self):
        """模板含未知变量时，应回退到代码默认 Prompt。"""
        resolved = _FakeResolved(
            node_key="writing.industry_profile",
            system_message="PROFILE_SYS",
            user_template="{project_context} {nonexistent_field}",
        )
        registry = _FakeRegistry(resolved)

        with patch(
            "app.core.writer.industry_profiler._get_reg",
            return_value=registry,
        ):
            from app.core.writer.industry_profiler import get_industry_profile_prompt

            sys_msg, user_msg = get_industry_profile_prompt(
                project_context="石油勘探",
                scoring_summary="技术20分",
                content_requirements="数值模拟",
                material_requirements="PVT数据",
            )

        assert "石油勘探" in user_msg
        assert "{nonexistent_field}" not in user_msg

    def test_registry_miss_uses_code_default(self):
        """Registry 未命中时，使用代码默认 Prompt。"""
        resolved = _FakeResolved(
            node_key="writing.industry_profile",
            system_message="",
            user_template="",
        )
        registry = _FakeRegistry(resolved)

        with patch(
            "app.core.writer.industry_profiler._get_reg",
            return_value=registry,
        ):
            from app.core.writer.industry_profiler import get_industry_profile_prompt

            sys_msg, user_msg = get_industry_profile_prompt(
                project_context="默认路径",
                scoring_summary="评分",
                content_requirements="要求",
                material_requirements="材料",
            )

        assert "默认路径" in user_msg
        assert "{project_context}" not in user_msg


# ────────────────────────────────────────────────
# outline_generator: _reg_or_code
# ────────────────────────────────────────────────

class TestOutlineGeneratorRegistryWrapper:

    def test_registry_hit_injects_kwargs(self):
        resolved = _FakeResolved(
            node_key="writing.skeleton_generate",
            system_message="OG_SYS",
            user_template="目录:{directory_rules} 评分:{scoring_criteria}",
        )
        registry = _FakeRegistry(resolved)

        with patch(
            "app.core.ai.prompt_registry.get_registry",
            return_value=registry,
        ):
            from app.core.writer.outline_generator import get_outline_generate_prompt
            sys_msg, user_msg = get_outline_generate_prompt(
                directory_rules="第一章 投标邀请函",
                scoring_criteria="技术方案30分",
                content_requirements="",
                material_requirements="",
                industry_profile="",
            )

        assert sys_msg == "OG_SYS"
        assert "第一章 投标邀请函" in user_msg
        assert "{directory_rules}" not in user_msg

    def test_registry_hit_bad_template_falls_back(self):
        resolved = _FakeResolved(
            node_key="writing.skeleton_generate",
            system_message="OG_SYS",
            user_template="{directory_rules} {nonexistent}",
        )
        registry = _FakeRegistry(resolved)

        with patch(
            "app.core.ai.prompt_registry.get_registry",
            return_value=registry,
        ):
            from app.core.writer.outline_generator import get_outline_generate_prompt
            sys_msg, user_msg = get_outline_generate_prompt(
                directory_rules="test",
                scoring_criteria="",
                content_requirements="",
                material_requirements="",
                industry_profile="",
            )

        assert "test" in user_msg
        assert "{nonexistent}" not in user_msg


# ────────────────────────────────────────────────
# material_tagger: get_material_tag_prompt
# ────────────────────────────────────────────────

class TestMaterialTaggerRegistryWrapper:

    def test_registry_hit_injects_kwargs(self):
        resolved = _FakeResolved(
            node_key="writing.material_tag",
            system_message="MT_SYS",
            user_template="标题:{title} 类型:{material_type}",
        )
        registry = _FakeRegistry(resolved)

        with patch(
            "app.core.ai.prompt_registry.get_registry",
            return_value=registry,
        ):
            from app.core.writer.material_tagger import get_material_tag_prompt
            sys_msg, user_msg = get_material_tag_prompt(
                title="营业执照",
                material_type="certificate",
                content_preview="xxx",
            )

        assert sys_msg == "MT_SYS"
        assert "营业执照" in user_msg
        assert "{title}" not in user_msg

    def test_registry_hit_bad_template_falls_back(self):
        resolved = _FakeResolved(
            node_key="writing.material_tag",
            system_message="MT_SYS",
            user_template="{title} {missing_var}",
        )
        registry = _FakeRegistry(resolved)

        with patch(
            "app.core.ai.prompt_registry.get_registry",
            return_value=registry,
        ):
            from app.core.writer.material_tagger import get_material_tag_prompt
            sys_msg, user_msg = get_material_tag_prompt(
                title="营业执照",
                material_type="cert",
                content_preview="xxx",
            )

        assert "营业执照" in user_msg
        assert "{missing_var}" not in user_msg


# ────────────────────────────────────────────────
# material_matcher: get_clause_classify_prompt / get_chapter_judge_prompt
# ────────────────────────────────────────────────

class TestMaterialMatcherRegistryWrapper:

    def test_clause_classify_injects_kwargs(self):
        resolved = _FakeResolved(
            node_key="writing.clause_classify",
            system_message="CC_SYS",
            user_template="约束列表:{clauses_text}",
        )
        registry = _FakeRegistry(resolved)

        with patch(
            "app.core.ai.prompt_registry.get_registry",
            return_value=registry,
        ):
            from app.core.writer.material_matcher import get_clause_classify_prompt
            sys_msg, user_msg = get_clause_classify_prompt(
                clauses_text="投标人资质要求",
            )

        assert sys_msg == "CC_SYS"
        assert "投标人资质要求" in user_msg

    def test_chapter_judge_injects_kwargs(self):
        resolved = _FakeResolved(
            node_key="writing.chapter_judge",
            system_message="CJ_SYS",
            user_template="章节:{chapter_title} 类型:{chapter_type}",
        )
        registry = _FakeRegistry(resolved)

        with patch(
            "app.core.ai.prompt_registry.get_registry",
            return_value=registry,
        ):
            from app.core.writer.material_matcher import get_chapter_judge_prompt
            sys_msg, user_msg = get_chapter_judge_prompt(
                chapter_title="技术方案",
                chapter_type="free_writing",
                chapter_path="",
                semantic_role="",
                expects="",
                rejects="",
                candidates_text="",
            )

        assert sys_msg == "CJ_SYS"
        assert "技术方案" in user_msg
        assert "{chapter_title}" not in user_msg

    def test_bad_template_falls_back(self):
        resolved = _FakeResolved(
            node_key="writing.clause_classify",
            system_message="CC_SYS",
            user_template="{clauses_text} {bad_var}",
        )
        registry = _FakeRegistry(resolved)

        with patch(
            "app.core.ai.prompt_registry.get_registry",
            return_value=registry,
        ):
            from app.core.writer.material_matcher import get_clause_classify_prompt
            sys_msg, user_msg = get_clause_classify_prompt(
                clauses_text="测试约束",
            )

        assert "测试约束" in user_msg
        assert "{bad_var}" not in user_msg


# ────────────────────────────────────────────────
# section_composer: _reg_or_code_sc
# ────────────────────────────────────────────────

class TestSectionComposerRegistryWrapper:

    def test_data_fill_injects_kwargs(self):
        resolved = _FakeResolved(
            node_key="writing.data_fill",
            system_message="DF_SYS",
            user_template="标题:{node_title} 字数:{target_word_count}",
        )
        registry = _FakeRegistry(resolved)

        with patch(
            "app.core.ai.prompt_registry.get_registry",
            return_value=registry,
        ):
            from app.core.writer.section_composer import get_data_fill_prompt
            sys_msg, user_msg = get_data_fill_prompt(
                node_title="人员配置表",
                target_word_count=500,
                clauses_text="",
                materials_text="",
            )

        assert sys_msg == "DF_SYS"
        assert "人员配置表" in user_msg
        assert "{node_title}" not in user_msg

    def test_free_writing_injects_kwargs(self):
        resolved = _FakeResolved(
            node_key="writing.free_writing",
            system_message="FW_SYS",
            user_template="标题:{node_title} 约束:{clauses_text}",
        )
        registry = _FakeRegistry(resolved)

        with patch(
            "app.core.ai.prompt_registry.get_registry",
            return_value=registry,
        ):
            from app.core.writer.section_composer import get_free_writing_prompt
            sys_msg, user_msg = get_free_writing_prompt(
                node_title="技术方案",
                target_word_count=1000,
                clauses_text="油藏模拟",
                materials_text="",
                previous_summaries_text="",
                industry_profile_excerpt="",
            )

        assert sys_msg == "FW_SYS"
        assert "油藏模拟" in user_msg

    def test_bad_template_falls_back(self):
        resolved = _FakeResolved(
            node_key="writing.data_fill",
            system_message="DF_SYS",
            user_template="{node_title} {unknown_field}",
        )
        registry = _FakeRegistry(resolved)

        with patch(
            "app.core.ai.prompt_registry.get_registry",
            return_value=registry,
        ):
            from app.core.writer.section_composer import get_data_fill_prompt
            sys_msg, user_msg = get_data_fill_prompt(
                node_title="设备清单",
                target_word_count=300,
                clauses_text="",
                materials_text="",
            )

        assert "设备清单" in user_msg
        assert "{unknown_field}" not in user_msg
