"""P0-C: 模型能力档案 / 能力路由 / 请求整形 防御测试。

锁定契约:
- 三态解析收紧优先: declared=unsupported 永远压制; observed 失败压过
  declared=supported; observed 成功把 auto 晋升为 supported;
- 能力路由: max_output 不足的通道被排除(qwen-max 8192 不得进入 14000 需求);
  strict/vision 显式不支持被排除; auto 放行; 过滤清空候选时 fail-open;
- thinking 参数按 provider 映射, unknown 厂商不发送任何参数;
- supports_seed=unsupported 时省略 seed;
- 旧 ai_settings JSON(无 capabilities 字段)兼容加载, 新字段可序列化往返。
"""

from __future__ import annotations

from typing import Any, Optional
from unittest.mock import patch

import pytest

from app.core.ai_capabilities import (
    ModelCapabilityProfile,
    ObservedCapability,
    effective_capability,
    effective_profile_summary,
    merge_probe_reports,
    resolve_tristate,
)
from app.core.ai.capability_router import (
    CallRequirements,
    build_requirements,
    capability_excluded_ids,
    channel_meets,
)
from app.core.ai.thinking_params import (
    build_thinking_extra_body,
    seed_allowed,
)
from app.core.ai_settings import ModelConfigItem


# ---------- 三态解析 ----------

def test_resolve_tristate_tightening_priority() -> None:
    ok = ObservedCapability(ok=True, probed_at="t", evidence="")
    fail = ObservedCapability(ok=False, probed_at="t", evidence="400")

    # declared unsupported 永远压制(即使探测成功)
    assert resolve_tristate("unsupported", ok) == "unsupported"
    # 探测失败压过人工声明的 supported(实测证据优先于放宽)
    assert resolve_tristate("supported", fail) == "unsupported"
    # 探测成功把 auto 晋升为 supported
    assert resolve_tristate("auto", ok) == "supported"
    # 双侧缺省保持 auto
    assert resolve_tristate("auto", None) == "auto"
    assert resolve_tristate("supported", None) == "supported"


def test_effective_capability_and_summary_sources() -> None:
    profile = ModelCapabilityProfile(
        strict_schema="supported",
        json_object="auto",
        observed={
            "strict_schema": ObservedCapability(ok=False, probed_at="t", evidence="rf 400"),
            "json_object": ObservedCapability(ok=True, probed_at="t", evidence=""),
        },
    )
    assert effective_capability(profile, "strict_schema") == "unsupported"
    assert effective_capability(profile, "json_object") == "supported"
    assert effective_capability(None, "json_object") == "auto"

    summary = effective_profile_summary(profile)
    assert summary["capabilities"]["strict_schema"]["effective"] == "unsupported"
    assert summary["capabilities"]["strict_schema"]["source"] == "observed"
    assert summary["capabilities"]["supports_seed"]["source"] == "default"


def test_merge_probe_reports_writes_observed() -> None:
    class _Report:
        def __init__(self, ok: bool, error: Optional[str]) -> None:
            self.ok = ok
            self.error = error
            self.probed_at = "2026-08-06T00:00:00+00:00"

    merged = merge_probe_reports(None, {
        "vision_basic": _Report(True, None),
        "strict_schema": _Report(False, "response_format unavailable"),
    })
    assert merged.observed["vision_basic"].ok is True
    assert merged.observed["strict_schema"].ok is False
    assert "unavailable" in merged.observed["strict_schema"].evidence
    assert merged.observed_at is not None


# ---------- 能力路由 ----------

class _FakeChannel:
    def __init__(self, profile: Optional[ModelCapabilityProfile]) -> None:
        self.client = type("C", (), {"capability_profile": profile})()


def test_requirements_derivation() -> None:
    req = build_requirements("chat_with_images", {"max_tokens": 1000})
    assert req.needs_vision is True and req.min_output_tokens == 1000

    req = build_requirements("chat_json", {"json_schema": {"type": "json_schema"}})
    assert req.structured_mode == "json_schema"

    req = build_requirements("chat_json", {"json_schema": None})
    assert req.structured_mode == "json_object"

    req = build_requirements("chat", {})
    assert req.structured_mode is None and req.needs_vision is False


def test_max_output_filter_excludes_small_model() -> None:
    """qwen-max(8192) 不得进入 14000 输出需求的候选(P0-C 核心场景)"""
    small = _FakeChannel(ModelCapabilityProfile(max_output_tokens=8192))
    large = _FakeChannel(ModelCapabilityProfile(max_output_tokens=131072))
    unknown = _FakeChannel(None)

    req = CallRequirements(min_output_tokens=14000)
    assert channel_meets(small, req) is False
    assert channel_meets(large, req) is True
    assert channel_meets(unknown, req) is True, "无档案(auto)必须放行"

    excluded = capability_excluded_ids([small, large, unknown], req, purpose="review")
    assert id(small) in excluded
    assert id(large) not in excluded


def test_strict_and_vision_filters() -> None:
    no_strict = _FakeChannel(ModelCapabilityProfile(strict_schema="unsupported"))
    with_strict = _FakeChannel(ModelCapabilityProfile(strict_schema="supported"))
    assert channel_meets(no_strict, CallRequirements(structured_mode="json_schema")) is False
    assert channel_meets(with_strict, CallRequirements(structured_mode="json_schema")) is True

    text_only = _FakeChannel(ModelCapabilityProfile(supports_vision="unsupported"))
    assert channel_meets(text_only, CallRequirements(needs_vision=True)) is False


def test_filter_fail_open_when_all_excluded() -> None:
    small_a = _FakeChannel(ModelCapabilityProfile(max_output_tokens=4096))
    small_b = _FakeChannel(ModelCapabilityProfile(max_output_tokens=8192))
    req = CallRequirements(min_output_tokens=14000)
    excluded = capability_excluded_ids([small_a, small_b], req, purpose="review")
    assert excluded == set(), "过滤清空候选时必须 fail-open 保业务不断流"


def test_filter_disabled_by_master_switch() -> None:
    small = _FakeChannel(ModelCapabilityProfile(max_output_tokens=1024))
    ok = _FakeChannel(None)
    req = CallRequirements(min_output_tokens=14000)
    with patch("app.core.config.settings.LLM_CAPABILITY_ROUTING_ENABLED", False):
        assert capability_excluded_ids([small, ok], req) == set()


# ---------- thinking 参数映射 ----------

def test_thinking_params_provider_mapping() -> None:
    disabled = ModelCapabilityProfile(thinking_policy="disabled")
    assert build_thinking_extra_body("qwen", disabled) == {"enable_thinking": False}
    assert build_thinking_extra_body("volcengine", disabled) == {"thinking": {"type": "disabled"}}
    assert build_thinking_extra_body("deepseek", disabled) == {"thinking": {"type": "disabled"}}

    effort = ModelCapabilityProfile(reasoning_effort="max")
    assert build_thinking_extra_body("deepseek", effort) == {"reasoning_effort": "max"}
    # tencent 不支持 max, 必须收敛到 high
    assert build_thinking_extra_body("tencent", effort) == {"reasoning_effort": "high"}

    # auto + default: 不发任何参数(保持现状)
    assert build_thinking_extra_body("deepseek", ModelCapabilityProfile()) == {}
    # 未识别厂商: 永不发送
    assert build_thinking_extra_body("unknown", disabled) == {}
    assert build_thinking_extra_body("baidu", disabled) == {}


def test_seed_omitted_when_unsupported() -> None:
    class _Client:
        capability_profile = ModelCapabilityProfile(supports_seed="unsupported")

    class _ClientAuto:
        capability_profile = None

    assert seed_allowed(_Client()) is False
    assert seed_allowed(_ClientAuto()) is True


# ---------- 持久化兼容 ----------

def test_legacy_config_without_capabilities_loads() -> None:
    legacy = {
        "id": "review_cloud_1", "api_base": "https://api.deepseek.com",
        "api_key": "sk-x", "model_name": "deepseek-v4-pro",
        "config_type": "cloud",
    }
    config = ModelConfigItem(**legacy)
    assert config.capabilities is None, "旧 JSON 必须兼容加载且不虚构能力"


def test_capabilities_roundtrip_serialization() -> None:
    config = ModelConfigItem(
        id="c1", api_base="https://api.deepseek.com", model_name="m",
        capabilities=ModelCapabilityProfile(
            max_output_tokens=8192, strict_schema="unsupported",
            observed={"json_object": ObservedCapability(ok=True, probed_at="t", evidence="")},
        ),
    )
    dumped = config.model_dump()
    restored = ModelConfigItem(**dumped)
    assert restored.capabilities is not None
    assert restored.capabilities.max_output_tokens == 8192
    assert restored.capabilities.strict_schema == "unsupported"
    assert restored.capabilities.observed["json_object"].ok is True


def test_declared_validation_rejects_bad_values() -> None:
    with pytest.raises(Exception):
        ModelCapabilityProfile(strict_schema="yes")  # 非法枚举
    with pytest.raises(Exception):
        ModelCapabilityProfile(max_output_tokens=10)  # 低于下限
