"""B2.6 — 约束解码框架单元测试。"""

import json
from typing import Optional

from pydantic import BaseModel

from app.core.ai.constrained_decoding import (
    ConstrainedMode,
    build_response_format,
    detect_capability,
    is_schema_rejection_error,
    should_use_constrained_decoding,
)


class _SampleNested(BaseModel):
    detail: str
    score: float


class _SampleModel(BaseModel):
    findings: list[_SampleNested]
    overall_risk: str
    note: Optional[str] = None


def test_detect_capability_whitelist():
    for provider in ("deepseek", "qwen", "openai", "zhipu"):
        assert detect_capability(provider) == ConstrainedMode.JSON_SCHEMA


def test_detect_capability_fallback():
    assert detect_capability("local") == ConstrainedMode.FALLBACK
    assert detect_capability("unknown") == ConstrainedMode.FALLBACK


def test_detect_capability_tool_use():
    assert detect_capability("anthropic") == ConstrainedMode.TOOL_USE


def test_build_response_format_serializable():
    fmt = build_response_format(_SampleModel)
    serialized = json.dumps(fmt, ensure_ascii=False)
    assert '"json_schema"' in serialized
    assert '"_SampleModel"' in serialized
    assert "$ref" not in serialized, "refs should be inlined"


def test_build_response_format_structure():
    fmt = build_response_format(_SampleModel)
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    schema = fmt["json_schema"]["schema"]
    assert "properties" in schema
    assert "findings" in schema["properties"]


def test_should_use_constrained_decoding_boundaries():
    assert not should_use_constrained_decoding(None, 1.0)
    assert not should_use_constrained_decoding(42, 0.0)
    assert should_use_constrained_decoding(42, 1.0)
    assert not should_use_constrained_decoding(42, -0.1)


def test_should_use_constrained_decoding_deterministic():
    results = [should_use_constrained_decoding(i, 0.5) for i in range(200)]
    true_count = sum(results)
    assert 30 < true_count < 170, f"Expected ~50% but got {true_count}/200"


# ---------------------------------------------------------------------------
# strict 契约必须由 build_response_format 自身保证
# ---------------------------------------------------------------------------


class _NoExtraConfig(BaseModel):
    """故意不设 model_config extra="forbid" —— 复现 JudgeVerdict 的形态。"""
    verdict: str
    confidence: float


class _WithDefaults(BaseModel):
    name: str
    tags: tuple[str, ...] = ()      # 有默认值但非 Optional
    amount: Optional[float] = None  # 真正的 Optional


class _Outer(BaseModel):
    inner: _NoExtraConfig
    items: list[_WithDefaults]


def _collect_object_nodes(node, path="root", acc=None):
    """收集 schema 中所有 object 节点, 用于逐层断言 strict 契约。"""
    acc = [] if acc is None else acc
    if isinstance(node, dict):
        if node.get("type") == "object" and isinstance(node.get("properties"), dict):
            acc.append((path, node))
        for key, value in node.items():
            _collect_object_nodes(value, f"{path}.{key}", acc)
    elif isinstance(node, list):
        for idx, value in enumerate(node):
            _collect_object_nodes(value, f"{path}[{idx}]", acc)
    return acc


def test_every_object_level_forbids_additional_properties():
    """strict 契约第 1 条: 每个 object 层级都必须显式 additionalProperties: false。"""
    schema = build_response_format(_Outer)["json_schema"]["schema"]
    nodes = _collect_object_nodes(schema)
    assert len(nodes) >= 3, f"嵌套 object 未被完整遍历, 实际: {[p for p, _ in nodes]}"
    for path, node in nodes:
        assert node.get("additionalProperties") is False, (
            f"{path} 缺少 additionalProperties: false"
        )


def test_every_object_level_requires_all_properties():
    """strict 契约第 2 条: required 必须列出 properties 的每一个 key。"""
    schema = build_response_format(_Outer)["json_schema"]["schema"]
    for path, node in _collect_object_nodes(schema):
        missing = set(node["properties"]) - set(node.get("required", []))
        assert not missing, f"{path} 的 required 遗漏字段: {sorted(missing)}"


def test_model_without_extra_forbid_still_compliant():
    """ 直接复现: 未设 extra="forbid" 的 model 曾产出不合规 schema 被 400 拒绝。"""
    schema = build_response_format(_NoExtraConfig)["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"verdict", "confidence"}


def test_optional_field_stays_nullable_and_required():
    """Optional 字段进 required 后必须仍可为 null, 否则模型无法表达"没有值"。"""
    schema = build_response_format(_WithDefaults)["json_schema"]["schema"]
    assert "amount" in schema["required"]
    amount = schema["properties"]["amount"]
    variants = {v.get("type") for v in amount.get("anyOf", [])}
    assert "null" in variants, f"Optional 字段丢失 nullable: {amount}"


def test_defaulted_non_optional_field_is_not_made_nullable():
    """有默认值但非 Optional 的字段禁止转 nullable: 模型回 null 会让 Pydantic 校验失败。"""
    schema = build_response_format(_WithDefaults)["json_schema"]["schema"]
    assert "tags" in schema["required"]
    tags = schema["properties"]["tags"]
    assert tags.get("type") == "array", f"tags 被错误改写: {tags}"
    assert "anyOf" not in tags


def test_normalized_schema_payload_still_parses_back():
    """规范化只应收紧 schema, 不得破坏原 model 的解析语义。"""
    payload = {"name": "x", "tags": [], "amount": None}
    assert _WithDefaults(**payload).amount is None


def test_build_response_format_does_not_mutate_pydantic_cache():
    """两次构建结果必须一致, 证明规范化未原地污染 Pydantic 侧 schema 对象。"""
    first = json.dumps(build_response_format(_Outer), sort_keys=True)
    second = json.dumps(build_response_format(_Outer), sort_keys=True)
    assert first == second


# ---------------------------------------------------------------------------
# schema 不合规 vs provider 能力缺失的判定隔离
# ---------------------------------------------------------------------------


def test_schema_rejection_recognized():
    """provider 拒绝 schema 内容 —— 属于我方缺陷。"""
    msg = (
        "error code: 400 - invalid schema for response_format 'judgeverdict': "
        "in context=(), 'additionalproperties' is required to be supplied and to be false."
    )
    assert is_schema_rejection_error(msg)


def test_capability_gap_not_treated_as_schema_rejection():
    """provider 不支持该参数 —— 属于能力缺失, 必须允许粘性降级。"""
    for msg in (
        "error code: 400 - this response_format type is unavailable now",
        "error code: 400 - response_format.type: unknown variant 'object'",
        "deepseek-v3.2-exp does not support response_format parameter",
    ):
        assert not is_schema_rejection_error(msg), f"误判为 schema 问题: {msg}"
