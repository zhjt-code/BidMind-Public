"""CloudClient 温度能力约束测试。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.ai.cloud_client import CloudClient


def _make_client(**kwargs) -> CloudClient:
    return CloudClient(
        base_url="https://example.com/v1",
        api_key="test-key",
        model_name="test-model",
        **kwargs,
    )


def test_user_default_overrides_business_temperature():
    """ 用户裁决: 用户显式默认温度压过业务显式温度。"""
    client = _make_client(default_temperature=0.7)
    assert client._resolve_temperature(0.3) == 0.7


def test_default_temperature_used_when_call_uses_signature_default():
    client = _make_client(default_temperature=0.7)
    assert client._resolve_temperature(0.1) == 0.7


def test_exact_temperature_overrides_explicit_business_value():
    client = _make_client(
        default_temperature=1.0,
        temperature_mode="exact",
        temperature_exact=1.0,
    )
    assert client._resolve_temperature(0.3) == 1.0


def test_clamp_temperature_applies_bounds():
    client = _make_client(
        temperature_mode="clamp",
        temperature_min=0.2,
        temperature_max=0.8,
    )
    assert client._resolve_temperature(0.05) == 0.2
    assert client._resolve_temperature(0.95) == 0.8
    assert client._resolve_temperature(0.5) == 0.5
