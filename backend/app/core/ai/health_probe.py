"""LLM 真实健康探测 (P0-B)

职责: 按能力维度对单个模型客户端执行真实探测, 返回结构化结果
不做: 不修改配置、不做调度决策、不落库(observed 持久化由 P0-C capability_store 承担)

探测原则(替代旧 "hi"+max_tokens=5 假绿探测):
- 文本: 必须返回非空正文(纯 think 计空), finish_reason 不得为 length/content_filter;
- JSON: response_format=json_object 且能解析出 dict/list;
- strict schema: 以微型 Pydantic schema 走约束解码, 区分能力缺失;
- 视觉: 必须发送真实小图并返回非空文本——文本 ping 不能证明视觉可用;
- 思考型模型预算保护: 文本探测 max_tokens=256, 防止思考 token 吃光正文。

所有探测流量标记 admin_test, 不污染生产可靠性统计。
"""

from __future__ import annotations

import asyncio
import base64
import io
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger
from pydantic import BaseModel

from app.core.ai.attempt_recorder import current_traffic_class

# 1x1 红色 PNG(硬编码兜底, Pillow 不可用时使用)
_FALLBACK_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4"
    "z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg=="
)

_probe_image_cache: Optional[str] = None

PROBE_TEXT_MAX_TOKENS = 256
PROBE_VISION_MAX_TOKENS = 512


def _probe_image_b64() -> str:
    """生成 64x64 纯色 PNG(部分厂商拒绝过小图片); 失败退回 1x1 硬编码"""
    global _probe_image_cache
    if _probe_image_cache is not None:
        return _probe_image_cache
    try:
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (64, 64), (200, 30, 30)).save(buf, format="PNG")
        _probe_image_cache = base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        _probe_image_cache = _FALLBACK_PNG_B64
    return _probe_image_cache


@dataclass
class ProbeReport:
    """单项探测结果"""

    kind: str
    ok: bool
    latency_ms: int = 0
    error: Optional[str] = None
    probed_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "ok": self.ok,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "probed_at": self.probed_at,
        }


class _ProbeEcho(BaseModel):
    """strict schema 探测用微型模型"""

    ok: bool
    note: str


def _is_bad_finish(client: Any) -> Optional[str]:
    finish = getattr(client, "_last_finish_reason", None)
    if finish in ("length", "content_filter"):
        return f"finish_reason={finish}"
    return None


async def _run_probe(kind: str, timeout: float, fn) -> ProbeReport:
    """统一包装: admin_test 流量标记 + 超时 + 异常归一"""
    token = current_traffic_class.set("admin_test")
    start = time.monotonic()
    try:
        error = await asyncio.wait_for(fn(), timeout=timeout)
        latency = int((time.monotonic() - start) * 1000)
        if error:
            return ProbeReport(kind=kind, ok=False, latency_ms=latency, error=error)
        return ProbeReport(kind=kind, ok=True, latency_ms=latency)
    except asyncio.TimeoutError:
        return ProbeReport(
            kind=kind, ok=False,
            latency_ms=int((time.monotonic() - start) * 1000),
            error=f"探测超时(>{timeout:.0f}s)",
        )
    except Exception as e:
        return ProbeReport(
            kind=kind, ok=False,
            latency_ms=int((time.monotonic() - start) * 1000),
            error=str(e)[:300],
        )
    finally:
        try:
            current_traffic_class.reset(token)
        except Exception:
            pass


async def probe_text_basic(client: Any, *, timeout: float = 15.0) -> ProbeReport:
    """文本基础探测: 非空正文 + finish_reason 合法"""
    async def _do() -> Optional[str]:
        content = await client.chat(
            user_message="请只回复两个字: 确认",
            max_tokens=PROBE_TEXT_MAX_TOKENS,
        )
        if not (content or "").strip():
            finish = getattr(client, "_last_finish_reason", None)
            return f"返回空正文(finish_reason={finish})"
        return _is_bad_finish(client)
    return await _run_probe("text_basic", timeout, _do)


async def probe_json_object(client: Any, *, timeout: float = 20.0) -> ProbeReport:
    """JSON Object 探测: 能返回可解析 JSON"""
    async def _do() -> Optional[str]:
        data = await client.chat_json(
            user_message='请以json格式返回 {"ok": true}',
            max_tokens=PROBE_TEXT_MAX_TOKENS,
        )
        if not isinstance(data, (dict, list)):
            return f"返回非 JSON 结构: {type(data).__name__}"
        return None
    return await _run_probe("json_object", timeout, _do)


async def probe_strict_schema(client: Any, *, timeout: float = 20.0) -> ProbeReport:
    """strict json_schema 探测: 约束解码是否真实可用"""
    async def _do() -> Optional[str]:
        from app.core.ai.cloud_client import UnsupportedResponseFormatError
        from app.core.ai.constrained_decoding import build_response_format
        schema = build_response_format(_ProbeEcho)
        try:
            data = await client.chat_json(
                user_message='请以json返回 {"ok": true, "note": "probe"}',
                max_tokens=PROBE_TEXT_MAX_TOKENS,
                json_schema=schema,
                propagate_unsupported=True,
            )
        except UnsupportedResponseFormatError as e:
            return f"不支持 response_format/json_schema: {str(e)[:160]}"
        if not isinstance(data, dict) or "ok" not in data:
            return "strict 输出未按 schema 返回"
        return None
    return await _run_probe("strict_schema", timeout, _do)


async def probe_vision_basic(client: Any, *, timeout: float = 25.0) -> ProbeReport:
    """视觉探测: 发送真实小图, 必须返回非空描述"""
    async def _do() -> Optional[str]:
        content = await client.chat_with_images(
            text_message="请用一句话描述这张图片的主色调。",
            image_data_list=[_probe_image_b64()],
            max_tokens=PROBE_VISION_MAX_TOKENS,
        )
        if not (content or "").strip():
            finish = getattr(client, "_last_finish_reason", None)
            return f"视觉返回空正文(finish_reason={finish})"
        return None
    return await _run_probe("vision_basic", timeout, _do)


async def run_primary_probe(
    client: Any, *, purpose: str, timeout: float = 15.0,
) -> ProbeReport:
    """按用途执行主探测: vision 池必须发真实图片, 其余走文本探测"""
    if purpose == "vision":
        return await probe_vision_basic(client, timeout=max(timeout, 25.0))
    return await probe_text_basic(client, timeout=timeout)


async def run_capability_probes(
    client: Any, *, purpose: str,
) -> dict[str, ProbeReport]:
    """完整能力探测套件(P0-C 保存并验证使用)"""
    reports: dict[str, ProbeReport] = {}
    reports["text_basic"] = await probe_text_basic(client)
    if purpose == "vision":
        reports["vision_basic"] = await probe_vision_basic(client)
    else:
        reports["json_object"] = await probe_json_object(client)
        reports["strict_schema"] = await probe_strict_schema(client)
    failed = [k for k, r in reports.items() if not r.ok]
    if failed:
        logger.info(f"能力探测存在失败项: model={getattr(client, 'model_name', '?')}, failed={failed}")
    return reports
