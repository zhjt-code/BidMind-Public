"""模型温度决议(用户能力约束 vs 业务节点意图)防御测试。

职责: 锁定 用户裁决优先级: exact/clamp 最高, 用户显式默认温度次之,
 业务节点温度仅在用户未设置时生效。
不做: 不发网络请求、不测 pdf_transport 载荷(见 test_pdf_transport)。
"""

from app.core.ai.temperature_params import (
    TemperatureSettings,
    resolve_request_temperature,
    temperature_settings_from_config,
)


def test_user_default_overrides_business_temperature():
    """ 用户裁决: 前端显式设置=最高等级, 压过业务显式温度(含0.0)。"""
    settings = TemperatureSettings(default_temperature=1.0, mode="free")
    assert resolve_request_temperature(0.0, settings) == 1.0
    assert resolve_request_temperature(0.3, settings) == 1.0
    assert resolve_request_temperature(0.1, settings) == 1.0


def test_business_temperature_kept_when_user_unset():
    """用户未设置(None)时业务节点温度原样生效。"""
    settings = TemperatureSettings(default_temperature=None, mode="free")
    assert resolve_request_temperature(0.0, settings) == 0.0
    assert resolve_request_temperature(0.3, settings) == 0.3


def test_exact_mode_overrides_explicit_business_value():
    settings = TemperatureSettings(
        default_temperature=1.0, mode="exact", exact=1.0,
    )
    assert resolve_request_temperature(0.0, settings) == 1.0
    assert resolve_request_temperature(0.3, settings) == 1.0


def test_clamp_mode_bounds_business_value():
    settings = TemperatureSettings(mode="clamp", min_value=0.2, max_value=0.8)
    assert resolve_request_temperature(0.05, settings) == 0.2
    assert resolve_request_temperature(0.95, settings) == 0.8
    assert resolve_request_temperature(0.5, settings) == 0.5


def test_none_settings_returns_requested():
    assert resolve_request_temperature(0.0, None) == 0.0


def test_settings_from_config_reads_user_fields():
    class _Cfg:
        temperature = 1.0
        temperature_mode = "exact"
        temperature_exact = 1.0
        temperature_min = None
        temperature_max = None

    settings = temperature_settings_from_config(_Cfg())
    assert settings.mode == "exact"
    assert settings.exact == 1.0
    assert settings.default_temperature == 1.0
