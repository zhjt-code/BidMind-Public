"""心跳字段编解码: Redis 不得把 bool 写成不可 json.loads 的 'True'。"""

from app.core.tasks.heartbeat_codec import (
    decode_heartbeat_field,
    encode_heartbeat_field,
    is_explicit_true,
)


def test_encode_bool_uses_json_token_not_python_repr() -> None:
    assert encode_heartbeat_field(True) == "true"
    assert encode_heartbeat_field(False) == "false"


def test_decode_legacy_python_bool_repr() -> None:
    """同形态夹具: 历史 str(True) 入库后仍应还原为 bool。"""
    assert decode_heartbeat_field("True") is True
    assert decode_heartbeat_field("False") is False


def test_decode_json_bool_token() -> None:
    assert decode_heartbeat_field("true") is True
    assert decode_heartbeat_field("false") is False
    assert decode_heartbeat_field(True) is True


def test_decode_does_not_coerce_progress_or_stage() -> None:
    assert decode_heartbeat_field("queued") == "queued"
    assert decode_heartbeat_field("0/1") == "0/1"
    assert decode_heartbeat_field("") == ""
    assert decode_heartbeat_field("1") == "1"


def test_is_explicit_true_rejects_nonempty_false_string() -> None:
    """审标 bool('False') 过宽; 跳过声明只认显式真值。"""
    assert is_explicit_true(True) is True
    assert is_explicit_true("True") is True
    assert is_explicit_true("true") is True
    assert is_explicit_true("False") is False
    assert is_explicit_true("false") is False
    assert is_explicit_true(None) is False
    assert is_explicit_true("") is False
