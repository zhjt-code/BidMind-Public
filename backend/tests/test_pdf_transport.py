"""chat直传PDF多方言传输适配防御测试。

覆盖: 四方言消息结构(openai_file/ark_file_url/dashscope_fileid/extract_text)、
      文件生命周期(上传->调用->finally删除)、unsupported错误映射与会话级降级、
      通道目标解析(能力档案声明 + DashScope派生 + 去重 + 会话过滤)。
不做: 不调真实API、不读真实配置文件(load_ai_settings 用桩替身)。
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from openai import BadRequestError

from app.core.ai import pdf_transport
from app.core.ai.pdf_transport import (
    PdfInputUnsupported,
    PdfTransportTarget,
    chat_with_pdf,
    resolve_pdf_transport_targets,
)

pytestmark = [pytest.mark.anyio, pytest.mark.scoring]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _clear_session_unsupported():
    pdf_transport._session_unsupported.clear()
    pdf_transport._session_no_temperature.clear()
    yield
    pdf_transport._session_unsupported.clear()
    pdf_transport._session_no_temperature.clear()


def _target(dialect: str, model: str = "m1", base: str = "https://api.test.com/v1", **kwargs) -> PdfTransportTarget:
    return PdfTransportTarget(
        config_id="cfg1", label=f"{model}@test", api_base=base,
        api_key="sk-test", model_name=model, dialect=dialect, timeout=30.0,
        **kwargs,
    )


class _FakeAsyncOpenAI:
    """AsyncOpenAI 替身: 记录消息载荷与文件生命周期, 行为经类属性注入。"""

    instances: list["_FakeAsyncOpenAI"] = []
    chat_error: Exception | None = None
    chat_errors: list[Exception] = []  # 一次性错误队列(优先于 chat_error)
    files_create_error: Exception | None = None
    completion_text: str = "OK"
    reasoning_content: str | None = None
    file_content_text: str = "解析文本" * 10

    def __init__(self, **kwargs):
        _FakeAsyncOpenAI.instances.append(self)
        self.init_kwargs = kwargs
        self.chat_payloads: list[dict] = []
        self.files_created: list[dict] = []
        self.files_deleted: list[str] = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._chat_create)
        )
        self.files = SimpleNamespace(
            create=self._files_create,
            delete=self._files_delete,
            content=self._files_content,
        )

    async def _chat_create(self, **kwargs):
        self.chat_payloads.append(kwargs)
        if _FakeAsyncOpenAI.chat_errors:
            raise _FakeAsyncOpenAI.chat_errors.pop(0)
        if _FakeAsyncOpenAI.chat_error is not None:
            raise _FakeAsyncOpenAI.chat_error
        message = SimpleNamespace(content=_FakeAsyncOpenAI.completion_text)
        if _FakeAsyncOpenAI.reasoning_content is not None:
            message.reasoning_content = _FakeAsyncOpenAI.reasoning_content
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )

    async def _files_create(self, **kwargs):
        if _FakeAsyncOpenAI.files_create_error is not None:
            raise _FakeAsyncOpenAI.files_create_error
        self.files_created.append(kwargs)
        return SimpleNamespace(id="file-abc123")

    async def _files_delete(self, file_id: str):
        self.files_deleted.append(file_id)

    async def _files_content(self, file_id: str):
        return SimpleNamespace(text=_FakeAsyncOpenAI.file_content_text)

    async def close(self):
        pass


@pytest.fixture(autouse=True)
def _fake_openai(monkeypatch):
    _FakeAsyncOpenAI.instances = []
    _FakeAsyncOpenAI.chat_error = None
    _FakeAsyncOpenAI.chat_errors = []
    _FakeAsyncOpenAI.files_create_error = None
    _FakeAsyncOpenAI.completion_text = "OK"
    _FakeAsyncOpenAI.reasoning_content = None
    monkeypatch.setattr(pdf_transport, "AsyncOpenAI", _FakeAsyncOpenAI)
    yield


def _bad_request(status: int = 400) -> BadRequestError:
    request = httpx.Request("POST", "https://api.test.com/v1/chat/completions")
    response = httpx.Response(status, request=request)
    return BadRequestError("unsupported", response=response, body=None)


async def _invoke(target: PdfTransportTarget) -> str:
    return await chat_with_pdf(
        target,
        pdf_bytes=b"%PDF-1.4 fake",
        pdf_base64="ZmFrZQ==",
        filename="doc1_scoring_p2-4.pdf",
        system_message="SYS",
        user_message="USER",
        temperature=0.0,
        max_tokens=1000,
    )


class TestFilePartDialects:
    async def test_openai_file_message_shape(self):
        text = await _invoke(_target("openai_file"))
        assert text == "OK"
        payload = _FakeAsyncOpenAI.instances[0].chat_payloads[0]
        assert payload["model"] == "m1"
        user_content = payload["messages"][1]["content"]
        file_part = user_content[0]
        assert file_part["type"] == "file"
        assert file_part["file"]["filename"] == "doc1_scoring_p2-4.pdf"
        assert file_part["file"]["file_data"].startswith("data:application/pdf;base64,")
        assert user_content[1] == {"type": "text", "text": "USER"}
        assert payload["messages"][0] == {"role": "system", "content": "SYS"}

    async def test_ark_file_url_message_shape(self):
        await _invoke(_target("ark_file_url"))
        user_content = _FakeAsyncOpenAI.instances[0].chat_payloads[0]["messages"][1]["content"]
        file_part = user_content[0]
        assert file_part["type"] == "file_url"
        assert file_part["file_url"]["url"].startswith("data:application/pdf;base64,")
        assert file_part["file_url"]["name"] == "doc1_scoring_p2-4.pdf"


class TestFileIdDialects:
    async def test_dashscope_fileid_flow_uploads_references_and_deletes(self):
        await _invoke(_target("dashscope_fileid", model="qwen-long"))
        fake = _FakeAsyncOpenAI.instances[0]
        assert fake.files_created[0]["purpose"] == "file-extract"
        messages = fake.chat_payloads[0]["messages"]
        assert messages[1] == {"role": "system", "content": "fileid://file-abc123"}
        assert messages[2] == {"role": "user", "content": "USER"}
        assert fake.files_deleted == ["file-abc123"]

    async def test_dashscope_file_deleted_even_when_chat_fails(self):
        _FakeAsyncOpenAI.chat_error = RuntimeError("boom")
        with pytest.raises(RuntimeError):
            await _invoke(_target("dashscope_fileid", model="qwen-long"))
        assert _FakeAsyncOpenAI.instances[0].files_deleted == ["file-abc123"]

    async def test_extract_text_flow_injects_parsed_text_and_deletes(self):
        await _invoke(_target("extract_text", model="kimi-k3"))
        fake = _FakeAsyncOpenAI.instances[0]
        messages = fake.chat_payloads[0]["messages"]
        assert messages[1]["role"] == "system"
        assert "解析文本" in messages[1]["content"]
        assert fake.files_deleted == ["file-abc123"]

    async def test_extract_text_clips_oversized_document(self, monkeypatch):
        from app.core import scoring_pdf_rescue_config as cfg_mod

        monkeypatch.setattr(
            cfg_mod.scoring_pdf_rescue_settings,
            "SCORING_PDF_RESCUE_EXTRACT_TEXT_MAX_CHARS", 8,
        )
        _FakeAsyncOpenAI.file_content_text = "0123456789ABCDEF"
        await _invoke(_target("extract_text", model="kimi-k3"))
        messages = _FakeAsyncOpenAI.instances[0].chat_payloads[0]["messages"]
        assert messages[1]["content"] == "01234567"


def _temperature_rejection_error() -> BadRequestError:
    request = httpx.Request("POST", "https://api.test.com/v1/chat/completions")
    response = httpx.Response(400, request=request)
    return BadRequestError(
        "invalid temperature: only 1 is allowed for this model",
        response=response,
        body={"message": "invalid temperature: only 1 is allowed for this model"},
    )


class TestTemperatureRejection:
    """: kimi-k3 类模型拒绝temperature参数, 不得误判为不支持PDF。"""

    async def test_retries_without_temperature_and_remembers(self):
        _FakeAsyncOpenAI.chat_errors = [_temperature_rejection_error()]
        target = _target("extract_text", model="kimi-k3")
        text = await _invoke(target)
        assert text == "OK"
        fake = _FakeAsyncOpenAI.instances[0]
        assert len(fake.chat_payloads) == 2
        assert "temperature" in fake.chat_payloads[0]
        assert "temperature" not in fake.chat_payloads[1]
        # 不得被误标为不支持; 会话记忆免下次白付400
        assert target.session_key not in pdf_transport._session_unsupported
        assert target.session_key in pdf_transport._session_no_temperature

    async def test_session_memory_skips_temperature_upfront(self):
        target = _target("openai_file", model="kimi-k3")
        pdf_transport._session_no_temperature.add(target.session_key)
        await _invoke(target)
        fake = _FakeAsyncOpenAI.instances[0]
        assert len(fake.chat_payloads) == 1
        assert "temperature" not in fake.chat_payloads[0]


class TestUserGenerationConstraints:
    """: 用户模型配置必须进入 PDF 直传请求(exact 最高, 缺省不压过显式 0.0)。"""

    async def test_exact_mode_overrides_business_temperature(self):
        from app.core.ai.temperature_params import TemperatureSettings

        target = _target(
            "extract_text", model="kimi-k3",
            temperature_settings=TemperatureSettings(
                default_temperature=1.0, mode="exact", exact=1.0,
            ),
        )
        await _invoke(target)
        payload = _FakeAsyncOpenAI.instances[0].chat_payloads[0]
        assert payload["temperature"] == 1.0

    async def test_free_mode_user_default_overrides_business_temperature(self):
        """ 用户裁决: 显式设置=最高等级, 业务显式 0.0 也被用户值压过。"""
        from app.core.ai.temperature_params import TemperatureSettings

        target = _target(
            "extract_text", model="kimi-k3",
            temperature_settings=TemperatureSettings(
                default_temperature=1.0, mode="free",
            ),
        )
        await _invoke(target)
        payload = _FakeAsyncOpenAI.instances[0].chat_payloads[0]
        assert payload["temperature"] == 1.0

    async def test_qwen_thinking_policy_injected(self):
        from app.core.ai_capabilities import ModelCapabilityProfile

        target = _target(
            "openai_file",
            model="qwen-long",
            base="https://dashscope.aliyuncs.com/compatible-mode/v1",
            capability_profile=ModelCapabilityProfile(thinking_policy="disabled"),
        )
        await _invoke(target)
        payload = _FakeAsyncOpenAI.instances[0].chat_payloads[0]
        assert payload.get("extra_body", {}).get("enable_thinking") is False

    def test_resolve_targets_copies_user_temperature_constraint(self, monkeypatch):
        configs = [
            _fake_config(
                "k", "https://api.moonshot.cn/v1", "kimi-k3",
                dialect="extract_text",
                temperature=1.0, temperature_mode="exact", temperature_exact=1.0,
            ),
        ]
        import app.core.ai_settings as ai_settings_mod

        monkeypatch.setattr(
            ai_settings_mod, "load_ai_settings",
            lambda: _fake_settings(configs, ["k"]),
        )
        targets = resolve_pdf_transport_targets()
        assert len(targets) == 1
        settings = targets[0].temperature_settings
        assert settings is not None
        assert settings.mode == "exact"
        assert settings.exact == 1.0


class TestUnsupportedMapping:
    async def test_unsupported_message_carries_provider_detail(self):
        request = httpx.Request("POST", "https://api.test.com/v1/chat/completions")
        response = httpx.Response(400, request=request)
        _FakeAsyncOpenAI.chat_error = BadRequestError(
            "text extract error: 没有解析出内容", response=response,
            body={"message": "text extract error: 没有解析出内容"},
        )
        with pytest.raises(PdfInputUnsupported) as excinfo:
            await _invoke(_target("openai_file"))
        assert "没有解析出内容" in str(excinfo.value)

    async def test_http_400_maps_to_unsupported_and_marks_session(self):
        _FakeAsyncOpenAI.chat_error = _bad_request(400)
        target = _target("openai_file")
        with pytest.raises(PdfInputUnsupported):
            await _invoke(target)
        assert target.session_key in pdf_transport._session_unsupported

    async def test_files_endpoint_missing_maps_to_unsupported(self):
        _FakeAsyncOpenAI.files_create_error = _bad_request(404)
        with pytest.raises(PdfInputUnsupported):
            await _invoke(_target("dashscope_fileid", model="qwen-long"))

    async def test_http_500_is_not_unsupported(self):
        request = httpx.Request("POST", "https://api.test.com/v1/chat/completions")
        response = httpx.Response(500, request=request)
        from openai import InternalServerError

        _FakeAsyncOpenAI.chat_error = InternalServerError(
            "down", response=response, body=None
        )
        target = _target("openai_file")
        with pytest.raises(InternalServerError):
            await _invoke(target)
        assert target.session_key not in pdf_transport._session_unsupported


def _fake_config(cid, base, model, dialect="auto", keys=("sk-1",), **extra):
    cfg = SimpleNamespace(
        id=cid, api_base=base, model_name=model, config_type="cloud",
        capabilities=SimpleNamespace(pdf_file_input=dialect),
        get_all_keys=lambda keys=keys: list(keys),
    )
    for key, value in extra.items():
        setattr(cfg, key, value)
    return cfg


def _fake_settings(configs, pool_ids):
    return SimpleNamespace(
        get_configs=lambda purpose: configs,
        get_pool_ids=lambda purpose: pool_ids,
    )


def _fake_settings_by_purpose(mapping, priority=None):
    """purpose -> (configs, pool_ids) 的池感知替身（scoring/review 双池场景）。"""
    return SimpleNamespace(
        get_configs=lambda purpose: mapping.get(purpose, ([], []))[0],
        get_pool_ids=lambda purpose: mapping.get(purpose, ([], []))[1],
        get_priority_for=lambda purpose, cid: (priority or {}).get(cid, 100),
    )


class TestResolveTargets:
    def test_declared_targets_in_pool_order_plus_dashscope_derived(self, monkeypatch):
        configs = [
            _fake_config("a", "https://ark.cn-beijing.volces.com/api/v3",
                         "doubao-seed", dialect="ark_file_url"),
            _fake_config("b", "https://dashscope.aliyuncs.com/compatible-mode/v1",
                         "qwen3.7-plus"),
            _fake_config("c", "https://api.deepseek.com", "deepseek-v4-pro"),
        ]
        import app.core.ai_settings as ai_settings_mod

        monkeypatch.setattr(
            ai_settings_mod, "load_ai_settings",
            lambda: _fake_settings(configs, ["b", "a", "c"]),
        )
        targets = resolve_pdf_transport_targets()
        assert [(t.model_name, t.dialect) for t in targets] == [
            ("doubao-seed", "ark_file_url"),
            ("qwen-long", "dashscope_fileid"),
        ]

    def test_session_unsupported_target_filtered(self, monkeypatch):
        configs = [
            _fake_config("a", "https://api.moonshot.cn/v1", "kimi-k3",
                         dialect="extract_text"),
        ]
        import app.core.ai_settings as ai_settings_mod

        monkeypatch.setattr(
            ai_settings_mod, "load_ai_settings",
            lambda: _fake_settings(configs, ["a"]),
        )
        targets = resolve_pdf_transport_targets()
        assert len(targets) == 1
        pdf_transport.mark_target_unsupported(targets[0])
        assert resolve_pdf_transport_targets() == []

    def test_scoring_pool_takes_precedence_over_review(self, monkeypatch):
        """评分专用池已配置时只消费该池, review 池不再参与目标解析。"""
        scoring_configs = [
            _fake_config("s1", "https://api.moonshot.cn/v1", "kimi-k3",
                         dialect="extract_text"),
        ]
        review_configs = [
            _fake_config("r1", "https://ark.cn-beijing.volces.com/api/v3",
                         "doubao-seed", dialect="ark_file_url"),
        ]
        import app.core.ai_settings as ai_settings_mod

        monkeypatch.setattr(
            ai_settings_mod, "load_ai_settings",
            lambda: _fake_settings_by_purpose({
                "scoring": (scoring_configs, ["s1"]),
                "review": (review_configs, ["r1"]),
            }),
        )
        targets = resolve_pdf_transport_targets()
        assert [(t.model_name, t.dialect) for t in targets] == [
            ("kimi-k3", "extract_text"),
        ]

    def test_empty_scoring_pool_falls_back_to_review(self, monkeypatch):
        """评分专用池为空时回退 review 池, 保持既有行为零回退。"""
        review_configs = [
            _fake_config("r1", "https://ark.cn-beijing.volces.com/api/v3",
                         "doubao-seed", dialect="ark_file_url"),
            _fake_config("r2", "https://dashscope.aliyuncs.com/compatible-mode/v1",
                         "qwen3.7-plus"),
        ]
        import app.core.ai_settings as ai_settings_mod

        monkeypatch.setattr(
            ai_settings_mod, "load_ai_settings",
            lambda: _fake_settings_by_purpose({
                "scoring": ([], []),
                "review": (review_configs, ["r1", "r2"]),
            }),
        )
        targets = resolve_pdf_transport_targets()
        assert [(t.model_name, t.dialect) for t in targets] == [
            ("doubao-seed", "ark_file_url"),
            ("qwen-long", "dashscope_fileid"),
        ]

    def test_auto_known_host_inferred_and_priority_order_respected(self, monkeypatch):
        """ 用户场景: kimi(auto,首选0)+doubao(声明,普通)+qwen-long(auto,次选50)
 —— auto 配置按已知主机推断方言, 目标顺序遵循 pool_priority, 派生目标去重。"""
        scoring_configs = [
            _fake_config("k", "https://api.moonshot.cn/v1", "kimi-k3"),
            _fake_config("d", "https://ark.cn-beijing.volces.com/api/v3",
                         "doubao-seed-evolving", dialect="openai_file"),
            _fake_config("q", "https://dashscope.aliyuncs.com/compatible-mode/v1",
                         "qwen-long"),
        ]
        import app.core.ai_settings as ai_settings_mod

        monkeypatch.setattr(
            ai_settings_mod, "load_ai_settings",
            lambda: _fake_settings_by_purpose(
                {"scoring": (scoring_configs, ["k", "d", "q"])},
                priority={"k": 0, "q": 50},
            ),
        )
        targets = resolve_pdf_transport_targets()
        assert [(t.model_name, t.dialect) for t in targets] == [
            ("kimi-k3", "extract_text"),
            ("qwen-long", "dashscope_fileid"),
            ("doubao-seed-evolving", "openai_file"),
        ]

    def test_declared_none_excludes_even_on_known_host(self, monkeypatch):
        """管理员显式 none 优先于主机推断(排除权威)。"""
        configs = [
            _fake_config("k", "https://api.moonshot.cn/v1", "kimi-k3",
                         dialect="none"),
        ]
        import app.core.ai_settings as ai_settings_mod

        monkeypatch.setattr(
            ai_settings_mod, "load_ai_settings",
            lambda: _fake_settings(configs, ["k"]),
        )
        assert resolve_pdf_transport_targets() == []

    def test_keyless_config_skipped(self, monkeypatch):
        configs = [
            _fake_config("a", "https://api.moonshot.cn/v1", "kimi-k3",
                         dialect="extract_text", keys=()),
        ]
        configs[0].api_key = ""
        import app.core.ai_settings as ai_settings_mod

        monkeypatch.setattr(
            ai_settings_mod, "load_ai_settings",
            lambda: _fake_settings(configs, ["a"]),
        )
        assert resolve_pdf_transport_targets() == []


class TestScoringPurposeSettings:
    """scoring 评分专用池在设置模型上的字段往返（第五 purpose 接线契约）。"""

    def test_scoring_purpose_roundtrip_on_settings_model(self):
        from app.core.ai_settings import AISettingsModel, ModelConfigItem

        model = AISettingsModel()
        config = ModelConfigItem(
            id="scoring_cloud_1", api_base="https://api.moonshot.cn/v1",
            api_key="sk-x", model_name="kimi-k3", config_type="cloud",
        )
        model.set_configs("scoring", [config])
        assert model.get_configs("scoring")[0].id == "scoring_cloud_1"
        assert model.get_config_by_id("scoring", "scoring_cloud_1") is config

        model.set_pool_ids("scoring", ["scoring_cloud_1"])
        assert model.get_pool_ids("scoring") == ["scoring_cloud_1"]

        model.set_pool_priority("scoring", {"scoring_cloud_1": 0})
        assert model.get_priority_for("scoring", "scoring_cloud_1") == 0

        model.set_model_id("scoring", "scoring_cloud_1")
        assert model.get_model_id("scoring") == "scoring_cloud_1"

        # 持久化字段存在(save/load 序列化契约)
        dumped = model.model_dump()
        assert dumped["scoring_configs"][0]["id"] == "scoring_cloud_1"
        assert dumped["scoring_pool_ids"] == ["scoring_cloud_1"]
        assert dumped["scoring_pool_priority"] == {"scoring_cloud_1": 0}

    def test_unknown_purpose_still_rejected(self):
        from app.core.ai_settings import AISettingsModel

        model = AISettingsModel()
        with pytest.raises(ValueError):
            model.get_configs("nonexistent")  # type: ignore[arg-type]


class TestEmptyCompletionFailover:
    async def test_reasoning_content_used_when_message_content_empty(self, monkeypatch):
        recorder = SimpleNamespace(success=[], error=[])
        recorder.record_success = lambda *a, **k: recorder.success.append(k)
        recorder.record_error = lambda *a, **k: recorder.error.append(k)
        monkeypatch.setattr(
            "app.core.ai.attempt_recorder.get_attempt_recorder", lambda: recorder
        )
        _FakeAsyncOpenAI.completion_text = ""
        _FakeAsyncOpenAI.reasoning_content = '{"type":"header"}'
        text = await chat_with_pdf(
            _target("extract_text"),
            pdf_bytes=b"%PDF", pdf_base64="cGRm", filename="a.pdf",
            system_message="SYS", user_message="USER",
            temperature=0.1, max_tokens=16, node_key="bid_review.scoring_pdf_rescue",
        )
        assert text == '{"type":"header"}'
        assert recorder.success

    async def test_empty_content_raises_not_unsupported(self, monkeypatch):
        from app.core.ai.pdf_transport import EmptyPdfCompletion

        recorder = SimpleNamespace(success=[], error=[])
        recorder.record_success = lambda *a, **k: recorder.success.append(k)
        recorder.record_error = lambda *a, **k: recorder.error.append(k)
        monkeypatch.setattr(
            "app.core.ai.attempt_recorder.get_attempt_recorder", lambda: recorder
        )
        _FakeAsyncOpenAI.completion_text = ""
        with pytest.raises(EmptyPdfCompletion):
            await chat_with_pdf(
                _target("extract_text"),
                pdf_bytes=b"%PDF", pdf_base64="cGRm", filename="a.pdf",
                system_message="SYS", user_message="USER",
                temperature=0.1, max_tokens=16,
            )
        assert recorder.success
        assert not recorder.error
