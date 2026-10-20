"""VL 图片 payload 归一化。

职责: 将 VL 图片 base64 归一化为云端 Base64 白名单格式和匹配的 data URL，并跳过边长不满足模型下限的图片。
不做: 不调用 LLM、不读取文件路径、不做 OCR/图片业务分类。
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, replace
from io import BytesIO
from typing import Sequence

from PIL import Image, UnidentifiedImageError


_SUPPORTED_FORMAT_TO_MIME = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "BMP": "image/bmp",
}
_CONVERTED_FORMAT = "PNG"
_CONVERTED_MIME = "image/png"
# qwen3-vl-plus: both sides must be larger than 10
_MIN_VISION_SIDE_PX = 11


@dataclass(frozen=True)
class VisionImagePayload:
    """可直接放入 OpenAI 兼容 image_url 的图片 payload。"""

    data_url: str
    mime_type: str
    source_format: str
    byte_size: int


@dataclass(frozen=True)
class SkippedVisionImage:
    """被跳过的图片摘要，不包含原始 base64 或完整图片内容。"""

    index: int
    reason: str
    source_format: str | None = None
    byte_size: int = 0
    width: int = 0
    height: int = 0


@dataclass(frozen=True)
class VisionImagePayloadBuildResult:
    """图片 payload 构建结果。"""

    payloads: list[VisionImagePayload]
    skipped: list[SkippedVisionImage]


def build_vision_image_payloads(
    image_data_list: Sequence[str],
) -> VisionImagePayloadBuildResult:
    """将图片 base64 列表转换为云端 VL 可接受的 data URL 列表。"""

    payloads: list[VisionImagePayload] = []
    skipped: list[SkippedVisionImage] = []

    for index, image_data in enumerate(image_data_list):
        raw = _decode_image_base64(image_data)
        if raw is None:
            skipped.append(SkippedVisionImage(index=index, reason="invalid_base64"))
            continue
        if not raw:
            skipped.append(SkippedVisionImage(index=index, reason="empty_image_data"))
            continue

        built = _build_single_payload(raw)
        if built is None:
            skipped.append(
                SkippedVisionImage(
                    index=index,
                    reason="unreadable_image",
                    byte_size=len(raw),
                )
            )
            continue
        if isinstance(built, SkippedVisionImage):
            skipped.append(replace(built, index=index))
            continue
        payloads.append(built)

    return VisionImagePayloadBuildResult(payloads=payloads, skipped=skipped)


def _decode_image_base64(image_data: str) -> bytes | None:
    data = (image_data or "").strip()
    if "," in data and data.lower().startswith("data:"):
        data = data.split(",", 1)[1].strip()
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        return None


def _build_single_payload(raw: bytes) -> VisionImagePayload | SkippedVisionImage | None:
    try:
        with Image.open(BytesIO(raw)) as image:
            image.load()
            source_format = (image.format or "UNKNOWN").upper()
            width, height = image.size
            if width < _MIN_VISION_SIDE_PX or height < _MIN_VISION_SIDE_PX:
                return SkippedVisionImage(
                    index=0,
                    reason="undersized_image",
                    source_format=source_format,
                    byte_size=len(raw),
                    width=width,
                    height=height,
                )
            if source_format in _SUPPORTED_FORMAT_TO_MIME:
                mime_type = _SUPPORTED_FORMAT_TO_MIME[source_format]
                normalized = raw
            else:
                normalized = _convert_to_png_bytes(image)
                mime_type = _CONVERTED_MIME
            encoded = base64.b64encode(normalized).decode("ascii")
            return VisionImagePayload(
                data_url=f"data:{mime_type};base64,{encoded}",
                mime_type=mime_type,
                source_format=source_format,
                byte_size=len(raw),
            )
    except (UnidentifiedImageError, OSError, ValueError):
        return None


def _convert_to_png_bytes(image: Image.Image) -> bytes:
    if image.mode not in {"RGB", "RGBA"}:
        image = image.convert("RGB")
    buf = BytesIO()
    image.save(buf, format=_CONVERTED_FORMAT)
    return buf.getvalue()
