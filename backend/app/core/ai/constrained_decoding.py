"""B2.6 — 约束解码能力检测与 response_format 构建。

职责: 把 Pydantic model 转为满足 OpenAI strict 契约的 response_format，并判定
      provider 的约束解码能力与错误归属。
不做: 不发起网络调用、不接触业务逻辑、不做降级编排。

- detect_capability: 判定 provider 是否支持 json_schema / tool_use
- build_response_format: 将 Pydantic model 转为 OpenAI 兼容 response_format
- is_schema_rejection_error: 区分"我方 schema 不合规" vs "provider 不支持该参数"
- should_use_constrained_decoding: 基于 project_id 与 ratio 判定灰度
"""

from __future__ import annotations

import copy
from enum import Enum
from typing import Any

from loguru import logger
from pydantic import BaseModel


class ConstrainedMode(str, Enum):
    JSON_SCHEMA = "json_schema"
    TOOL_USE = "tool_use"
    FALLBACK = "fallback"


_JSON_SCHEMA_PROVIDERS = frozenset({
    "deepseek", "qwen", "openai", "zhipu", "siliconflow",
    "volcengine", "openrouter",
})

_TOOL_USE_PROVIDERS = frozenset({
    "anthropic",
})


def detect_capability(provider: str, model_name: str = "") -> ConstrainedMode:
    """判定 provider 的约束解码能力。

    首批白名单参见 facts_first_harness_plan.md §5.4 B2.6。
    """
    p = provider.lower()
    if p in _JSON_SCHEMA_PROVIDERS:
        return ConstrainedMode.JSON_SCHEMA
    if p in _TOOL_USE_PROVIDERS:
        return ConstrainedMode.TOOL_USE
    return ConstrainedMode.FALLBACK


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """内联 $defs/$ref，避免部分 provider 不支持 $ref。"""
    defs = schema.pop("$defs", None) or schema.pop("definitions", None) or {}
    if not defs:
        return schema

    def _resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                ref_path = node["$ref"]
                ref_name = ref_path.rsplit("/", 1)[-1]
                if ref_name in defs:
                    resolved = copy.deepcopy(defs[ref_name])
                    return _resolve(resolved)
                return node
            return {k: _resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_resolve(item) for item in node]
        return node

    return _resolve(schema)


def _normalize_strict_schema(node: Any) -> Any:
    """递归把 JSON Schema 改造成满足 OpenAI strict 契约的形态。

    职责: 对每个 object 节点补齐 strict 模式的两条硬性要求。
    不做: 不改动字段类型、不做 nullable 转换（见下方说明）。

    strict 契约（OpenAI Structured Outputs）:
    1. 每个 object 层级必须显式声明 additionalProperties: false
    2. required 必须列出 properties 的每一个 key

    第 2 条对"有默认值但非 Optional"的字段（如 tuple[X, ...] = ()）只入 required
    而不转 nullable：这类字段的 Pydantic 类型不接受 None，若转成可空会让模型有权
    输出 null 并在回程校验时炸掉。强制模型显式输出空数组才是语义正确的做法。
    """
    if isinstance(node, dict):
        if isinstance(node.get("properties"), dict):
            node["properties"] = {
                key: _normalize_strict_schema(sub)
                for key, sub in node["properties"].items()
            }
        for key in ("anyOf", "oneOf", "allOf", "prefixItems"):
            if isinstance(node.get(key), list):
                node[key] = [_normalize_strict_schema(sub) for sub in node[key]]
        items = node.get("items")
        if isinstance(items, dict):
            node["items"] = _normalize_strict_schema(items)
        elif isinstance(items, list):
            node["items"] = [_normalize_strict_schema(sub) for sub in items]

        if node.get("type") == "object" and isinstance(node.get("properties"), dict):
            node["additionalProperties"] = False
            node["required"] = list(node["properties"].keys())
    return node


def build_response_format(model_cls: type[BaseModel]) -> dict[str, Any]:
    """将 Pydantic model 转为 OpenAI 兼容 response_format。: 本函数硬编码 strict=True，因此必须自己保证 schema 满足 strict 契约。
 此前把该责任隐式外包给调用方的 model_config（设了 extra="forbid" 的 model 才会
 带上 additionalProperties），导致 JudgeVerdict 这类未设 extra 的 model 被 provider
 以 400 拒绝。规范化统一在此收口，调用方无需再关心 model_config。

 Returns:
 {"type": "json_schema", "json_schema": {"name": ..., "schema": ..., "strict": True}}
 """
    # deepcopy 隔离 Pydantic 侧对象，避免后续原地规范化污染其内部结构
    raw_schema = copy.deepcopy(model_cls.model_json_schema())
    inlined = _inline_refs(raw_schema)

    inlined.pop("title", None)
    inlined.pop("description", None)

    normalized = _normalize_strict_schema(inlined)
    name = model_cls.__name__

    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "schema": normalized,
            "strict": True,
        },
    }


# provider 返回的 400 有两种性质完全不同的来源——
# (a) 不支持 response_format / json_schema 这个参数本身（能力缺失，应粘性降级）
# (b) 支持该参数，但拒绝我们发出的 schema 内容（我方缺陷，禁止粘性降级）
# 下列标记均为 (b) 的特征：OpenAI schema 校验器的固定措辞与定位前缀。
_SCHEMA_REJECTION_MARKERS = (
    "invalid schema",
    "additionalproperties",
    "in context=",
    "is required to be supplied",
)


def is_schema_rejection_error(err_msg: str) -> bool:
    """判定错误是否属于"我方 schema 不合规"而非"provider 不支持该参数"。

    命中时调用方必须跳过粘性能力缓存：把一次坏 schema 记成"该模型没有约束解码能力"
    会让一个健康通道在整个进程生命周期内退化为普通 chat。
    """
    lowered = err_msg.lower()
    return any(marker in lowered for marker in _SCHEMA_REJECTION_MARKERS)


def should_use_constrained_decoding(
    project_id: int | None,
    ratio: float,
) -> bool:
    """基于 project_id 的 hash 与灰度比例判定是否开启约束解码。"""
    if ratio <= 0.0 or project_id is None:
        return False
    if ratio >= 1.0:
        return True
    return (hash(project_id) % 100) < int(ratio * 100)
