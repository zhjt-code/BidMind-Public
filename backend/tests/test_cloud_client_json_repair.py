"""CloudClient JSON 边界提取与截断修复回归测试。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.core.ai.cloud_client import CloudClient


def _client_without_network(monkeypatch: pytest.MonkeyPatch) -> CloudClient:
    """构造不初始化 OpenAI 客户端的 CloudClient 测试实例。"""
    monkeypatch.setattr(
        CloudClient,
        "_record_json_repair",
        staticmethod(lambda **_: None),
    )
    return CloudClient.__new__(CloudClient)


def test_extract_json_payload_keeps_brackets_inside_string() -> None:
    raw = '[{"description":"要求[一级资质]或ISO9001","keywords":["一级"]}]'

    payload = CloudClient._extract_first_json_payload(raw)
    parsed = json.loads(payload)

    assert len(parsed) == 1
    assert parsed[0]["description"] == "要求[一级资质]或ISO9001"


def test_extract_json_payload_keeps_braces_inside_string() -> None:
    raw = '[{"a":"含{嵌套}内容"},{"b":"正常"}]'

    payload = CloudClient._extract_first_json_payload(raw)
    parsed = json.loads(payload)

    assert len(parsed) == 2
    assert parsed[0]["a"] == "含{嵌套}内容"


def test_extract_json_payload_supports_single_object() -> None:
    raw = '{"a":"含[字符]"}'

    payload = CloudClient._extract_first_json_payload(raw)

    assert json.loads(payload) == {"a": "含[字符]"}


def test_repair_truncated_object_keeps_complete_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client_without_network(monkeypatch)

    repaired = client._try_repair_json('{"a":"完整","b":"残缺')

    assert repaired == {"a": "完整"}


def test_chat_json_handles_markdown_with_brackets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client_without_network(monkeypatch)

    async def fake_chat(**_: Any) -> str:
        return '```json\n[{"description":"要求[一级资质]","keywords":["一级"]}]\n```'

    monkeypatch.setattr(client, "chat", fake_chat)

    result = asyncio.run(client.chat_json(user_message="ignored"))

    assert result == [{"description": "要求[一级资质]", "keywords": ["一级"]}]


def test_try_repair_json_returns_none_for_empty_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client_without_network(monkeypatch)

    assert client._try_repair_json("   ") is None


def test_repair_truncated_array_still_recovers_complete_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client_without_network(monkeypatch)

    repaired = client._try_repair_json('[{"a":"完整"},{"b":"保留"},{"c":"残缺')

    assert repaired == [{"a": "完整"}, {"b": "保留"}]


def test_extract_payload_preserves_array_prefix_when_decoder_fails() -> None:
    """: 数组开头但 raw_decode 失败时，必须保留 [ 起始的原文，
 禁止退化为后续第一个完整 {...} 对象，否则破坏数组语义并丢失元素。"""
    truncated = '[{"node_id":1,"role":"r1"},{"node_id":2,"role":"r2"},{"node_id":3,"role'

    payload = CloudClient._extract_first_json_payload(truncated)

    assert payload.startswith("[")
    assert '{"node_id":1' in payload
    assert '{"node_id":2' in payload


def test_chat_json_recovers_truncated_array_via_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """: 端到端验证 chat_json 在 LLM 返回被截断的数组时，
 能通过 _repair_truncated_array 抢救已完成的画像项，而非降级为单对象。"""
    client = _client_without_network(monkeypatch)

    truncated_array = (
        '[{"node_id":1,"semantic_role":"技术方案","expects":["e1"],"rejects":["r1","r2"]},'
        '{"node_id":2,"semantic_role":"服务承诺","expects":["e2"],"rejects":["r3","r4"]},'
        '{"node_id":3,"semantic_role":"残缺","expects":["e3"],"reje'
    )

    async def fake_chat(**_: Any) -> str:
        return truncated_array

    monkeypatch.setattr(client, "chat", fake_chat)

    result = asyncio.run(client.chat_json(user_message="ignored"))

    assert isinstance(result, list), (
        "截断数组必须被抢救为 list，禁止降级为单条对象"
    )
    assert len(result) == 2, "应抢救出前两条完整的画像项"
    assert {item["node_id"] for item in result} == {1, 2}


def test_extract_payload_preserves_object_prefix_when_decoder_fails() -> None:
    """: 对象开头但 raw_decode 失败时(典型为截断的顶层对象)，
 必须保留 { 起始的原文交由 _try_repair_json 抢救，禁止在提取层直接
 降级为后续第一个完整 {...}（可能是答案的内嵌小对象）。"""
    truncated = '{"scheme":"scheme_1","scoring_basis":{"expected_total":100.0},"facts":[{"category":"TE'

    payload = CloudClient._extract_first_json_payload(truncated)

    assert payload.startswith('{"scheme"')
    assert '"facts"' in payload, "截断顶层对象原文必须完整保留给修复层"


def test_chat_json_truncated_top_object_not_degraded_to_inner_fragment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ 端到端: 顶层对象被 max_tokens 截断时，chat_json 必须经
 对象截断修复返回含顶层完整字段的 dict，而非内嵌的评分基准小对象
 {"expected_total": 100.0}（合法 JSON 但是错误答案，比解析失败更危险）。"""
    client = _client_without_network(monkeypatch)

    truncated_object = (
        '{"scheme": "scheme_1", "lot": "__single__", '
        '"scoring_basis": {"expected_total": 100.0}, '
        '"facts": [{"category": "TECH", "score_item_name": "设计任务书优化", "descri'
    )

    async def fake_chat(**_: Any) -> str:
        return truncated_object

    monkeypatch.setattr(client, "chat", fake_chat)

    result = asyncio.run(client.chat_json(user_message="ignored"))

    assert isinstance(result, dict)
    assert result != {"expected_total": 100.0}, (
        "禁止把内嵌小对象当作答案返回"
    )
    assert result.get("scheme") == "scheme_1", "顶层完整字段必须被抢救保留"
    assert result.get("lot") == "__single__"


def test_chat_json_junk_brace_prefix_still_rescued_via_strategy5(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """首个 { 是非 JSON 占位文本时，原"内嵌对象扫描"降级迁移到修复策略5，
    端到端行为保持: 仍能取到其后的完整 JSON 对象，无需重试。"""
    client = _client_without_network(monkeypatch)

    async def fake_chat(**_: Any) -> str:
        return '{不是合法JSON但开头 {"valid":"object"}'

    monkeypatch.setattr(client, "chat", fake_chat)

    result = asyncio.run(client.chat_json(user_message="ignored"))

    assert result == {"valid": "object"}


# ---------------------------------------------------------------------------
# 截断扩容知识随 LlmContentValidationError 带出
# ---------------------------------------------------------------------------


def test_chat_with_validation_truncation_carries_suggested_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """截断类失败重试耗尽后, 异常必须携带扩容后的 suggested_max_tokens,
 且第2次尝试确实使用扩容后的预算(激活的自愈链)。"""
    from pydantic import BaseModel

    from app.core.ai.cloud_client import LlmContentValidationError

    client = _client_without_network(monkeypatch)
    client.model_name = "fake-model"

    seen_tokens: list[int] = []

    async def fake_chat_json(**kwargs: Any) -> Any:
        seen_tokens.append(kwargs.get("max_tokens"))
        raise json.JSONDecodeError("Expecting value", "", 0)

    monkeypatch.setattr(client, "chat_json", fake_chat_json)

    class _M(BaseModel):
        a: int

    async def _run() -> None:
        with pytest.raises(LlmContentValidationError) as exc_info:
            await client.chat_with_validation(
                user_message="u", response_model=_M,
                max_tokens=1000, max_retries=2,
            )
        # 第1次 1000 截断→扩容2000; 第2次 2000 截断→扩容4000 后耗尽
        assert seen_tokens == [1000, 2000], f"实际: {seen_tokens}"
        assert exc_info.value.suggested_max_tokens == 4000

    asyncio.run(_run())


def test_chat_with_validation_schema_failure_no_suggested_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """纯 schema 校验失败(无截断)时不得携带扩容建议——避免污染下游预算。"""
    from pydantic import BaseModel

    from app.core.ai.cloud_client import LlmContentValidationError

    client = _client_without_network(monkeypatch)
    client.model_name = "fake-model"

    async def fake_chat_json(**kwargs: Any) -> Any:
        return {"wrong_field": 1}

    monkeypatch.setattr(client, "chat_json", fake_chat_json)

    class _M(BaseModel):
        a: int

    async def _run() -> None:
        with pytest.raises(LlmContentValidationError) as exc_info:
            await client.chat_with_validation(
                user_message="u", response_model=_M,
                max_tokens=1000, max_retries=1,
            )
        assert exc_info.value.suggested_max_tokens is None

    asyncio.run(_run())
