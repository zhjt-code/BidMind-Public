from tests.synthetic_data import synthetic_text

"""预览图片内联防御测试

锁定: 预览图片渲染必须与 Word 渲染 (DocRenderer._insert_figure_images)
判定一致 -- 章节亲和过滤、文件缺失、未绑定、独占图跨章节去重、占位顺序消费。
对应用户需求: 预览页"所见即所得", 避免回归 图片顽疾。
"""

import base64
from types import SimpleNamespace

from app.core.writer.preview_image_inliner import (
    extract_image_map,
    inline_section_images,
)
from app.models.writing_material_image import ImageType


def _make_record(image_path: str, **overrides) -> SimpleNamespace:
    base = dict(
        id=7,
        image_path=image_path,
        caption="图1: 营业执照",
        image_type=ImageType.license,
        business_subtype="business_license",
        width=0,
        height=0,
        file_hash="hash-7",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_extract_image_map_preserves_order_and_dedup() -> None:
    raw = (
        '<figure data-image-id="5"><figcaption>第一</figcaption></figure>'
        '<figure data-image-id="8"><figcaption>第二</figcaption></figure>'
        '<figure data-image-id="5"><figcaption>重复</figcaption></figure>'
    )
    assert extract_image_map(raw) == [("5", "重复"), ("8", "第二")]


def test_inline_replaces_placeholder_with_base64_image(tmp_path) -> None:
    img_file = tmp_path / "license.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\n-fake-license-bytes")
    rec = _make_record(str(img_file))
    rendered = "<p>我方持有营业执照如下。</p>\n<p>[图片占位]</p>"
    raw = '<figure data-image-id="7"><figcaption>图1: 营业执照</figcaption></figure>'

    out = inline_section_images(rendered, raw, {7: rec}, "营业执照", {})

    assert "[图片占位]" not in out
    assert "data:image/png;base64," in out
    assert base64.b64encode(img_file.read_bytes()).decode("ascii") in out
    assert "图1: 营业执照" in out
    assert "<figure" in out and "<img" in out


def test_inline_marks_pending_when_image_not_affine_to_section(tmp_path) -> None:
    img_file = tmp_path / "license.png"
    img_file.write_bytes(b"license-bytes")
    rec = _make_record(str(img_file))
    rendered = "<p>[图片占位]</p>"
    raw = '<figure data-image-id="7"><figcaption>营业执照</figcaption></figure>'

    # 营业执照图片放到"技术方案"章节 -> 亲和闸门拦截
    out = inline_section_images(rendered, raw, {7: rec}, "技术方案", {})

    assert "data:image" not in out
    assert "图片待人工确认" in out


def test_inline_marks_missing_when_file_absent() -> None:
    rec = _make_record("/path/does/not/exist/license.png")
    rendered = "<p>[图片占位]</p>"
    raw = '<figure data-image-id="7"><figcaption>营业执照</figcaption></figure>'

    out = inline_section_images(rendered, raw, {7: rec}, "营业执照", {})

    assert "图片缺失" in out
    assert "data:image" not in out


def test_inline_marks_pending_when_record_not_in_section_pool() -> None:
    rendered = "<p>[图片占位]</p>"
    raw = '<figure data-image-id="99"><figcaption>某图</figcaption></figure>'

    out = inline_section_images(rendered, raw, {}, "营业执照", {})

    assert "图片待人工确认" in out
    assert "data:image" not in out


def test_inline_blocks_duplicate_exclusive_image_across_sections(tmp_path) -> None:
    img_file = tmp_path / "idcard.png"
    img_file.write_bytes(b"idcard-bytes")
    rec = _make_record(
        str(img_file),
        id=3,
        caption="法定代表人身份证",
        image_type=ImageType.personnel_cert,
        business_subtype="id_card",
        file_hash="dup-key",
    )
    consumed: dict[str, str] = {}
    rendered = "<p>[图片占位]</p>"
    raw = '<figure data-image-id="3"><figcaption>法定代表人身份证</figcaption></figure>'

    first = inline_section_images(rendered, raw, {3: rec}, "法定代表人身份证明", consumed)
    assert "data:image" in first

    # id_card 关键词同样匹配"授权委托", 亲和通过但应被去重拦截
    second = inline_section_images(rendered, raw, {3: rec}, "授权委托书", consumed)
    assert "data:image" not in second
    assert "图片待人工确认" in second


def test_inline_consumes_placeholders_in_document_order(tmp_path) -> None:
    file_a = tmp_path / "a.png"
    file_a.write_bytes(b"AAA-bytes")
    file_b = tmp_path / "b.png"
    file_b.write_bytes(b"BBB-bytes")
    rec_a = _make_record(str(file_a), id=1, caption="图A", file_hash="a")
    rec_b = _make_record(str(file_b), id=2, caption="图B", file_hash="b")
    rendered = "<p>[图片占位]</p>\n<p>中间正文</p>\n<p>[图片占位]</p>"
    raw = (
        '<figure data-image-id="1"><figcaption>图A</figcaption></figure>'
        '<figure data-image-id="2"><figcaption>图B</figcaption></figure>'
    )

    out = inline_section_images(rendered, raw, {1: rec_a, 2: rec_b}, "营业执照", {})

    b64_a = base64.b64encode(b"AAA-bytes").decode("ascii")
    b64_b = base64.b64encode(b"BBB-bytes").decode("ascii")
    assert b64_a in out and b64_b in out
    assert out.index(b64_a) < out.index(b64_b)


def test_inline_renders_identity_cards_as_ordered_two_column_layout(tmp_path) -> None:
    files = {}
    for name, payload in {
        "legal-front": b"legal-front",
        "legal-back": b"legal-back",
        "agent-front": b"agent-front",
        "agent-back": b"agent-back",
    }.items():
        path = tmp_path / f"{name}.png"
        path.write_bytes(payload)
        files[name] = path

    records = {
        4: _make_record(
            str(files["agent-back"]),
            id=4,
            caption="委托代理人身份证反面",
            image_type=ImageType.personnel_cert,
            business_subtype="id_card",
            vl_content="签发机关 XX公安局 有效期限 2020-2030",
            structured_fields="{}",
            material_id=1,
            image_index=3,
        ),
        2: _make_record(
            str(files["legal-back"]),
            id=2,
            caption="法定代表人身份证反面",
            image_type=ImageType.personnel_cert,
            business_subtype="id_card",
            vl_content="签发机关 XX公安局 有效期限 2020-2030",
            structured_fields="{}",
            material_id=1,
            image_index=1,
        ),
        3: _make_record(
            str(files["agent-front"]),
            id=3,
            caption="委托代理人身份证正面",
            image_type=ImageType.personnel_cert,
            business_subtype="id_card",
            vl_content="",
            structured_fields=synthetic_text('{"姓名": "<PERSON_B>", "证件号码": "', '110101', '199001', '011234', '"}'),
            material_id=1,
            image_index=2,
        ),
        1: _make_record(
            str(files["legal-front"]),
            id=1,
            caption="法定代表人身份证正面",
            image_type=ImageType.personnel_cert,
            business_subtype="id_card",
            vl_content="",
            structured_fields=synthetic_text('{"姓名": "<PERSON_A>", "证件号码": "', '110101', '198001', '011234', '"}'),
            material_id=1,
            image_index=0,
        ),
    }
    rendered = "\n".join("<p>[图片占位]</p>" for _ in range(4))
    raw = (
        '<figure data-image-id="4"><figcaption>委托代理人身份证反面</figcaption></figure>'
        '<figure data-image-id="2"><figcaption>法定代表人身份证反面</figcaption></figure>'
        '<figure data-image-id="3"><figcaption>委托代理人身份证正面</figcaption></figure>'
        '<figure data-image-id="1"><figcaption>法定代表人身份证正面</figcaption></figure>'
    )

    out = inline_section_images(rendered, raw, records, "法定代表人身份证明及授权委托书", {})

    assert "identity-card-layout" in out
    assert "[图片占位]" not in out
    encoded_order = [
        base64.b64encode(files["legal-front"].read_bytes()).decode("ascii"),
        base64.b64encode(files["legal-back"].read_bytes()).decode("ascii"),
        base64.b64encode(files["agent-front"].read_bytes()).decode("ascii"),
        base64.b64encode(files["agent-back"].read_bytes()).decode("ascii"),
    ]
    assert [out.index(encoded) for encoded in encoded_order] == sorted(
        out.index(encoded) for encoded in encoded_order
    )


def test_inline_returns_unchanged_when_no_figure_in_raw_html() -> None:
    rendered = "<p>纯文本章节, 无图片。</p>"

    out = inline_section_images(rendered, "<p>no figure here</p>", {}, "章节", {})

    assert out == rendered
