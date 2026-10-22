"""B2.4 — ContextVar 降级信号 push/drain 验证。"""

import json

from app.core.contracts.degradation_signal import (
    DegradationSignal,
    current_degradation_signals,
    drain_signals,
    push_signal,
)


def test_push_and_drain_in_same_context():
    """同一协程上下文中 push 后 drain 应返回全部信号并清空。"""
    current_degradation_signals.set(None)

    push_signal(DegradationSignal(
        source="json_repair_truncated",
        flow="collusion",
        repair_type="truncated_array",
        original_length=5000,
        recovered_elements=3,
    ))
    push_signal(DegradationSignal(
        source="json_repair_before_error",
        flow="collusion",
        repair_type="before_error",
        original_length=4000,
        recovered_elements=2,
    ))

    signals = drain_signals()
    assert len(signals) == 2
    assert signals[0].source == "json_repair_truncated"
    assert signals[1].recovered_elements == 2

    assert drain_signals() == []


def test_cloud_client_repair_pushes_signal():
    """CloudClient._repair_truncated_array 修复成功时应 push 信号。"""
    current_degradation_signals.set(None)

    from app.core.ai.cloud_client import CloudClient
    client = CloudClient.__new__(CloudClient)

    truncated_json = json.dumps([{"a": 1}, {"b": 2}])[:-1]  # 去掉末尾 ]
    result = client._repair_truncated_array(truncated_json)

    assert result is not None
    assert len(result) >= 1

    signals = drain_signals()
    assert len(signals) == 1
    assert signals[0].source == "json_repair_truncated"
    assert signals[0].repair_type == "truncated_array"


def test_no_signal_on_valid_json():
    """非数组 JSON 不匹配 repair 入口，不应产生信号。"""
    current_degradation_signals.set(None)

    from app.core.ai.cloud_client import CloudClient
    client = CloudClient.__new__(CloudClient)

    valid_dict = json.dumps({"a": 1})
    result = client._repair_truncated_array(valid_dict)
    assert result is None

    assert drain_signals() == []
