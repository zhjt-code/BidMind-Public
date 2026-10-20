"""防御测试：data_fill 生成路径消费行业画像。

风险映射：
- ``fill_data`` 历史上接收 ``industry_profile`` 却从未在函数体引用（死参数），
 资料填充产出的表格与短段落因此完全不带行业术语
 （docs/implementation_plan.md:302 记录的遗留未完成项）
- 教训：``writing.data_fill`` 已在 LLM Center 注册，线上激活模板不含新
 占位符时，新增上下文会被 Registry 模板静默吞掉，且不报错、无日志
- 输入超长降级时若把画像排在前面裁剪，等于新增能力上线即失效
"""

from __future__ import annotations

import os
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import APIStatusError

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test"
)

from app.core.writer.prompts.section_compose import (
    DATA_FILL_USER,
    get_data_fill_prompt,
)
from app.core.writer.section_composer import SectionComposer

# 复刻生产库六段式画像形态，各节埋入可断言标记词
_REALISTIC_PROFILE = """### 1. 行业识别与典型特征
行业特征标记：城市燃气输配运维，带气作业资质为准入门槛。

### 2. 专业术语表（20-30个）
1. 调压箱
2. 阴极保护
3. 带气作业
术语表标记

### 3. 技术方案典型结构
技术结构标记：先做管网现状评估，再给出分区抢修方案。

### 4. 评标专家核心关注点（5-8个）
评标关注点标记：抢修到场时限与应急演练频次。

### 5. 写作风格指导
写作风格标记：统一使用规范术语，避免口语化表达。

### 6. 差异化竞争建议
差异化建议标记：突出自有抢修车队与压力监测平台。"""

_PROFILE_SECTION_TITLE = "## 行业术语与表达要求"
_PROFILE_PLACEHOLDER = "{industry_profile_excerpt}"
_EMPTY_PROFILE_HINT = "（无行业画像）"
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")

# 旧 active 模板（LLM Center 线上版本），不含本次新增占位符
_LEGACY_REGISTRY_TEMPLATE = (
    "OLD_TEMPLATE_MARKER\n"
    "## 章节信息\n标题: {node_title}\n\n"
    "## 可用素材\n{materials_text}"
)


@pytest.fixture(autouse=True)
def pinned_code_default_registry(monkeypatch):
    """把 Registry 固定为未命中，使断言只针对代码默认模板。

    Registry 是进程级单例，其他测试模块可能已把线上模板预热进来。不固定时，
    本模块对代码默认模板文案（如占位提示语）的断言会随执行顺序漂移：
    单文件运行通过、与其他模块混跑失败。
    """
    import app.core.ai.prompt_registry as reg_mod

    monkeypatch.setattr(
        reg_mod,
        "get_registry",
        lambda: _FakeRegistry(_FakeResolvedPrompt(None, None)),
    )


def _stub_registry_user_template(monkeypatch, template: str) -> None:
    """把 Registry 用户模板解析替换为固定返回值。

    ``get_data_fill_prompt`` 在函数体内延迟 import，故 patch 必须打到
    ``app.core.ai.prompt_registry`` 本身。
    """
    import app.core.ai.prompt_registry as reg_mod

    monkeypatch.setattr(
        reg_mod,
        "resolve_user_template",
        lambda node_key, code_default: template,
        raising=True,
    )


class _ScriptedChatClient:
    """按脚本依次返回结果或抛出异常的 LLM 客户端替身。"""

    def __init__(self, scripted: list[object]) -> None:
        self._scripted = list(scripted)
        self.calls: list[dict] = []

    async def chat(self, **kwargs) -> str:
        self.calls.append(kwargs)
        current = self._scripted.pop(0)
        if isinstance(current, Exception):
            raise current
        return str(current)


def _make_input_too_long_error() -> APIStatusError:
    """构造云端 input-too-long 400，用于触发语义降级重试。"""
    message = "Range of input length should be [1, 30720]"
    request = httpx.Request("POST", "https://example.com/v1/chat/completions")
    response = httpx.Response(status_code=400, request=request)
    return APIStatusError(
        message, response=response, body={"error": {"message": message}}
    )


def _build_composer(llm_client) -> SectionComposer:
    """构造只保留 prompt 渲染链路的 composer（DB 依赖全部 mock 为空）。"""
    composer = SectionComposer(db=None, llm_client=llm_client)
    composer._load_bound_materials = AsyncMock(return_value=[])  # type: ignore[method-assign]
    composer._load_bound_clauses = AsyncMock(return_value=[])  # type: ignore[method-assign]
    composer._load_material_chunk_samples = AsyncMock(return_value={})  # type: ignore[method-assign]
    composer._load_material_images = AsyncMock(return_value=[])  # type: ignore[method-assign]
    return composer


class TestDataFillPromptTemplate:
    """代码默认模板的占位符与指令契约。"""

    def test_template_contains_profile_placeholder(self):
        assert _PROFILE_PLACEHOLDER in DATA_FILL_USER
        assert _PROFILE_SECTION_TITLE in DATA_FILL_USER

    def test_profile_section_precedes_chapter_profile(self):
        """项目级画像必须排在章节级画像之前（从宽到窄）。"""
        assert DATA_FILL_USER.index(_PROFILE_SECTION_TITLE) < DATA_FILL_USER.index(
            "## 章节画像"
        )

    def test_task_requires_terminology_usage(self):
        """仅注入画像不足以保证消费深度，任务段必须给出术语使用指令。"""
        task_section = DATA_FILL_USER.split("## 任务", 1)[1]
        assert "术语" in task_section
        assert "通俗说法" in task_section
        # 术语指令不得反向诱导编造事实
        assert "编造" in task_section


def test_data_fill_seed_declares_industry_profile_excerpt() -> None:
    """: DATA_FILL_USER 占位符必须全部登记到 PARAM_DEFS input。"""
    from scripts.seed_llm_registry import PARAM_DEFS

    data_fill_inputs = {
        param.param_key
        for param in PARAM_DEFS
        if param.node_key == "writing.data_fill" and param.direction == "input"
    }
    placeholders = set(_PLACEHOLDER_RE.findall(DATA_FILL_USER))

    assert "industry_profile_excerpt" in data_fill_inputs
    assert placeholders.issubset(data_fill_inputs), (
        f"writing.data_fill 模板占位符未同步 ParamDef: "
        f"{sorted(placeholders - data_fill_inputs)}"
    )


class TestGetDataFillPrompt:
    """工厂函数的向后兼容与注入行为。"""

    def test_missing_profile_keeps_backward_compatible(self):
        """不传画像参数仍可 format，占位符被替换为提示文案。"""
        _system, user = get_data_fill_prompt(
            node_title="人员配置表",
            target_word_count=500,
            clauses_text="",
            materials_text="",
        )

        assert _EMPTY_PROFILE_HINT in user
        assert _PROFILE_PLACEHOLDER not in user

    def test_profile_excerpt_injected(self):
        _system, user = get_data_fill_prompt(
            node_title="人员配置表",
            target_word_count=500,
            clauses_text="",
            materials_text="",
            industry_profile_excerpt="术语表：调压箱、阴极保护",
        )

        assert "术语表：调压箱、阴极保护" in user
        assert _EMPTY_PROFILE_HINT not in user


class TestRegistryLegacyTemplateCompatibility:
    """线上模板未同步时画像不得丢失（同源风险）。"""

    def test_legacy_template_still_carries_profile(self, monkeypatch):
        _stub_registry_user_template(monkeypatch, _LEGACY_REGISTRY_TEMPLATE)

        _system, user = get_data_fill_prompt(
            node_title="人员配置表",
            target_word_count=500,
            clauses_text="",
            materials_text="",
            industry_profile_excerpt="术语表：调压箱、阴极保护",
        )

        # 仍走 Registry 模板（未整体回退代码默认），且画像被补挂
        assert "OLD_TEMPLATE_MARKER" in user
        assert _PROFILE_SECTION_TITLE in user
        assert "术语表：调压箱、阴极保护" in user

    def test_legacy_template_without_profile_appends_nothing(self, monkeypatch):
        """画像为空时不得追加空标题段，避免注入无信息量内容。"""
        _stub_registry_user_template(monkeypatch, _LEGACY_REGISTRY_TEMPLATE)

        _system, user = get_data_fill_prompt(
            node_title="人员配置表",
            target_word_count=500,
            clauses_text="",
            materials_text="",
        )

        assert "OLD_TEMPLATE_MARKER" in user
        assert _PROFILE_SECTION_TITLE not in user
        assert _PROFILE_PLACEHOLDER not in user

    def test_broken_template_falls_back_and_keeps_profile(self, monkeypatch):
        """Registry 模板占位符被误删时回退代码默认，画像仍随之保留。"""
        _stub_registry_user_template(monkeypatch, "BROKEN {not_a_real_key}")

        _system, user = get_data_fill_prompt(
            node_title="人员配置表",
            target_word_count=500,
            clauses_text="",
            materials_text="",
            industry_profile_excerpt="术语表：调压箱、阴极保护",
        )

        assert "章节信息" in user
        assert "术语表：调压箱、阴极保护" in user


class TestFillDataRouteConsumption:
    """路由层：industry_profile 参数必须真正抵达 LLM 请求。"""

    async def test_profile_reaches_llm_with_purpose_scoped_sections(self):
        llm_client = SimpleNamespace(chat=AsyncMock(return_value="<p>生成成功</p>"))
        composer = _build_composer(llm_client)
        node = SimpleNamespace(
            id=7,
            title="人员配置表",
            depth=1,
            target_word_count=500,
            chapter_profile=None,
        )

        await composer._fill_data(
            node,
            industry_profile=_REALISTIC_PROFILE,
            number_map={7: "一、"},
        )

        user_msg = llm_client.chat.await_args.kwargs["user_message"]
        # data_fill 场景只取术语表 + 写作风格，其余段落不进上下文
        assert "术语表标记" in user_msg
        assert "写作风格标记" in user_msg
        assert "差异化建议标记" not in user_msg
        assert "技术结构标记" not in user_msg
        assert "评标关注点标记" not in user_msg

    async def test_missing_profile_does_not_break_generation(self):
        llm_client = SimpleNamespace(chat=AsyncMock(return_value="<p>生成成功</p>"))
        composer = _build_composer(llm_client)
        node = SimpleNamespace(
            id=8,
            title="设备清单",
            depth=1,
            target_word_count=500,
            chapter_profile=None,
        )

        payload = await composer._fill_data(
            node,
            industry_profile=None,
            number_map={8: "二、"},
        )

        assert "生成成功" in payload.content
        assert _EMPTY_PROFILE_HINT in llm_client.chat.await_args.kwargs["user_message"]

    async def test_unparsable_profile_degrades_to_head_truncation(self):
        """画像为失败兜底文案（无六段结构）时退回头部截断，不得整段丢弃。"""
        llm_client = SimpleNamespace(chat=AsyncMock(return_value="<p>生成成功</p>"))
        composer = _build_composer(llm_client)
        node = SimpleNamespace(
            id=9,
            title="业绩明细表",
            depth=1,
            target_word_count=500,
            chapter_profile=None,
        )

        await composer._fill_data(
            node,
            industry_profile="行业画像生成失败，请人工补充。无结构化标题。",
            number_map={9: "三、"},
        )

        user_msg = llm_client.chat.await_args.kwargs["user_message"]
        assert "行业画像生成失败，请人工补充" in user_msg

    async def test_profile_is_cropped_last_on_input_overflow(self):
        """输入超长降级时先裁约束，画像处于最低裁剪优先级。"""
        llm_client = _ScriptedChatClient(
            [_make_input_too_long_error(), "<p>生成成功</p>"]
        )
        composer = _build_composer(llm_client)
        composer._load_bound_clauses = AsyncMock(  # type: ignore[method-assign]
            return_value=[
                SimpleNamespace(
                    id=index,
                    sub_type="content_required",
                    content=f"内容要求{index}" * 6,
                    metadata_json=None,
                )
                for index in range(12)
            ]
        )
        node = SimpleNamespace(
            id=10,
            title="资质汇总表",
            depth=1,
            target_word_count=500,
            chapter_profile=None,
        )

        payload = await composer._fill_data(
            node,
            industry_profile=_REALISTIC_PROFILE,
            number_map={10: "四、"},
        )

        assert "生成成功" in payload.content
        assert len(llm_client.calls) == 2
        first_msg = str(llm_client.calls[0]["user_message"])
        retried_msg = str(llm_client.calls[1]["user_message"])
        assert len(retried_msg) < len(first_msg)
        assert "内容要求11" not in retried_msg
        assert "术语表标记" in retried_msg
        assert "写作风格标记" in retried_msg


class _FakeResolvedPrompt:
    """模拟 Registry 解析结果，只暴露 wrapper 实际消费的字段。

    system_message / user_template 传 None 表示未命中，业务侧回退代码默认。
    """

    def __init__(
        self, system_message: str | None, user_template: str | None
    ) -> None:
        self.node_key = "writing.data_fill"
        self.system_message = system_message
        self.user_template = user_template
        self.temperature = None
        self.max_tokens = None


class _FakeRegistry:
    def __init__(self, resolved: _FakeResolvedPrompt) -> None:
        self._resolved = resolved

    def get_prompt(self, node_key: str) -> _FakeResolvedPrompt:
        return self._resolved


class TestProductionWrapperCarriesProfile:
    """ 教训：必须断言生产 wrapper 的最终消息，而非内层工厂。

 ``fill_data`` 实际调用的是 ``section_composer.get_data_fill_prompt``。
 该 wrapper 若经 ``call_with_fallback`` 解析，Registry 命中时会直接格式化
 线上模板并静默忽略多余入参，使代码侧补挂失效——生产环境 Registry 已发布
 该节点，画像会 100% 丢失，而单测 patch 内层函数时完全测不出来。
 """

    def test_registry_hit_still_carries_profile(self, monkeypatch):
        from unittest.mock import patch

        from app.core.writer.section_composer import (
            get_data_fill_prompt as production_get_data_fill_prompt,
        )

        registry = _FakeRegistry(
            _FakeResolvedPrompt("DF_SYS", _LEGACY_REGISTRY_TEMPLATE)
        )
        with patch(
            "app.core.ai.prompt_registry.get_registry", return_value=registry
        ):
            system, user = production_get_data_fill_prompt(
                node_title="人员配置表",
                target_word_count=500,
                clauses_text="",
                materials_text="",
                industry_profile_excerpt="术语表：调压箱、阴极保护",
            )

        assert system == "DF_SYS"
        assert "OLD_TEMPLATE_MARKER" in user
        assert _PROFILE_SECTION_TITLE in user
        assert "术语表：调压箱、阴极保护" in user

    def test_registry_hit_without_profile_keeps_legacy_template_clean(self):
        from unittest.mock import patch

        from app.core.writer.section_composer import (
            get_data_fill_prompt as production_get_data_fill_prompt,
        )

        registry = _FakeRegistry(
            _FakeResolvedPrompt("DF_SYS", _LEGACY_REGISTRY_TEMPLATE)
        )
        with patch(
            "app.core.ai.prompt_registry.get_registry", return_value=registry
        ):
            _system, user = production_get_data_fill_prompt(
                node_title="人员配置表",
                target_word_count=500,
                clauses_text="",
                materials_text="",
            )

        assert "OLD_TEMPLATE_MARKER" in user
        assert _PROFILE_SECTION_TITLE not in user
