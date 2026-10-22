"""云端 VL 图片 payload 归一化防御测试。"""

from __future__ import annotations

import base64
from io import BytesIO

from PIL import Image

from app.core.ai.vision_image_payload import build_vision_image_payloads


def _image_b64(
    fmt: str,
    *,
    mode: str = "RGB",
    size: tuple[int, int] = (16, 16),
) -> str:
    image = Image.new(mode, size, color=(12, 34, 56))
    buf = BytesIO()
    image.save(buf, format=fmt)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _decode_data_url(data_url: str) -> tuple[str, bytes]:
    prefix, b64 = data_url.split(",", 1)
    return prefix, base64.b64decode(b64)


def test_supported_base64_formats_keep_matching_mime() -> None:
    """JPG/PNG/BMP 属于云端 Base64 白名单，应保持真实 MIME。"""

    result = build_vision_image_payloads([
        _image_b64("JPEG"),
        _image_b64("PNG"),
        _image_b64("BMP"),
    ])

    assert result.skipped == []
    assert [payload.mime_type for payload in result.payloads] == [
        "image/jpeg",
        "image/png",
        "image/bmp",
    ]
    assert all(payload.data_url.startswith(f"data:{payload.mime_type};base64,") for payload in result.payloads)


def test_webp_gif_tiff_are_converted_to_cloud_base64_whitelist() -> None:
    """WEBP/GIF/TIFF 可本地打开但不在 Base64 白名单内，发送前必须转换。"""

    result = build_vision_image_payloads([
        _image_b64("WEBP"),
        _image_b64("GIF"),
        _image_b64("TIFF"),
    ])

    assert result.skipped == []
    assert len(result.payloads) == 3
    assert all(payload.mime_type in {"image/jpeg", "image/png", "image/bmp"} for payload in result.payloads)
    assert all(payload.source_format in {"WEBP", "GIF", "TIFF"} for payload in result.payloads)
    for payload in result.payloads:
        prefix, normalized_bytes = _decode_data_url(payload.data_url)
        assert prefix == f"data:{payload.mime_type};base64"
        with Image.open(BytesIO(normalized_bytes)) as opened:
            opened.load()
            assert opened.format in {"JPEG", "PNG", "BMP"}


def test_data_url_input_is_accepted_and_normalized() -> None:
    """调用方如果已带 data URL 前缀，边界层仍应能识别并输出规范 data URL。"""

    prefixed = f"data:image/png;base64,{_image_b64('PNG')}"

    result = build_vision_image_payloads([prefixed])

    assert result.skipped == []
    assert len(result.payloads) == 1
    assert result.payloads[0].mime_type == "image/png"
    assert result.payloads[0].data_url.startswith("data:image/png;base64,")


def test_invalid_base64_and_corrupt_images_are_skipped_without_raw_payload() -> None:
    """非法输入应本地跳过，且摘要中不得携带完整 base64。"""

    truncated_jpeg = _image_b64("JPEG")[:24]
    result = build_vision_image_payloads([
        "not valid base64",
        base64.b64encode(b"not an image").decode("ascii"),
        truncated_jpeg,
    ])

    assert result.payloads == []
    assert [item.reason for item in result.skipped] == [
        "invalid_base64",
        "unreadable_image",
        "unreadable_image",
    ]
    skipped_text = repr(result.skipped)
    assert "not valid base64" not in skipped_text
    assert truncated_jpeg not in skipped_text


def test_undersized_images_are_skipped_without_raw_payload() -> None:
    """任一边长 <=10 会被 qwen3-vl-plus 拒收，必须本地跳过且不夹带原文。"""

    result = build_vision_image_payloads([
        _image_b64("PNG", size=(7, 100)),
        _image_b64("PNG", size=(100, 7)),
        _image_b64("PNG", size=(10, 11)),
        _image_b64("PNG", size=(11, 10)),
    ])

    assert result.payloads == []
    assert [item.reason for item in result.skipped] == ["undersized_image"] * 4
    assert [(item.width, item.height) for item in result.skipped] == [
        (7, 100),
        (100, 7),
        (10, 11),
        (11, 10),
    ]
    skipped_text = repr(result.skipped)
    assert "iVBORw0KGgo" not in skipped_text


def test_min_accepted_side_is_eleven_pixels() -> None:
    """两边均大于 10 的图片应放行。"""

    result = build_vision_image_payloads([_image_b64("PNG", size=(11, 11))])

    assert result.skipped == []
    assert len(result.payloads) == 1
