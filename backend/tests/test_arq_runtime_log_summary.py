"""ARQ 入队日志参数摘要单元测试。

防御目标 (BUG-XXX, ARQ 日志 base64 污染):
- 标量与短字符串保留可读性
- 长字符串、容器、字节串只输出 ``<type:len=N>`` 形式
- 任何含 base64/二进制 payload 的 dict/list 不会泄漏内容到日志
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.arq_runtime import _summarize_arq_args  # noqa: E402


def test_scalars_pass_through() -> None:
    summary = _summarize_arq_args((1, 3.14, True, False, None))
    assert summary == "(1, 3.14, True, False, None)"


def test_short_string_uses_repr() -> None:
    summary = _summarize_arq_args(("bid_review", "doc-123"))
    assert summary == "('bid_review', 'doc-123')"


def test_long_string_replaced_with_marker() -> None:
    long_text = "a" * 5000
    summary = _summarize_arq_args((long_text,))
    assert "<str:len=5000>" in summary
    assert "aaaa" not in summary


def test_list_only_shows_length() -> None:
    items = [{"k": "v"}, {"k": "v2"}]
    summary = _summarize_arq_args((1153, "bid_review", "batch-id", items))
    assert "<list:len=2>" in summary
    assert "{" not in summary


def test_dict_payload_with_base64_not_leaked() -> None:
    fake_b64 = "iVBORw0KGgo" + "A" * 2000
    payload = [{
        "image_key": "full_page|114",
        "kind": "full_page",
        "page_b64": fake_b64,
        "page_num": 114,
        "context": "宁东油田",
    }]
    summary = _summarize_arq_args((1153, "bid_review", "batch", payload))
    assert "<list:len=1>" in summary
    assert fake_b64 not in summary
    assert "page_b64" not in summary
    assert "iVBORw" not in summary


def test_tuple_set_dict_summary_form() -> None:
    summary = _summarize_arq_args(((1, 2, 3), {1, 2}, {"a": 1, "b": 2}))
    assert "<tuple:len=3>" in summary
    assert "<set:len=2>" in summary
    assert "<dict:keys=2>" in summary


def test_bytes_summary_form() -> None:
    summary = _summarize_arq_args((b"binary-data" * 100, bytearray(50)))
    assert "<bytes:len=" in summary
    assert "<bytearray:len=50>" in summary
    assert "binary-data" not in summary


def test_custom_object_only_class_name() -> None:
    class _MyClass:
        def __repr__(self) -> str:
            return "should-not-appear-in-log"

    summary = _summarize_arq_args((_MyClass(),))
    assert "<_MyClass>" in summary
    assert "should-not-appear-in-log" not in summary


def test_empty_args_returns_empty_tuple_string() -> None:
    assert _summarize_arq_args(()) == "()"


def test_string_at_boundary_length_kept() -> None:
    # 边界: 长度 80 字符串保留 repr
    s80 = "x" * 80
    summary = _summarize_arq_args((s80,))
    assert repr(s80) in summary

    # 边界: 长度 81 字符串改用 marker
    s81 = "x" * 81
    summary = _summarize_arq_args((s81,))
    assert "<str:len=81>" in summary
