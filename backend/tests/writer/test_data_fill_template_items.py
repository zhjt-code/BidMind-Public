""": data_fill模板应消费图片结构化字段，而非只生成空占位行。"""

import json
from types import SimpleNamespace

import pytest

from app.core.writer.composer.data_fill_route import (
    extract_template_items,
    try_data_fill_template,
)


def test_qualification_template_items_use_structured_fields() -> None:
    material = SimpleNamespace(id=697, title="三体系证书.pdf", material_type="text")
    image = SimpleNamespace(
        material_id=697,
        image_type="qualification",
        structured_fields=json.dumps(
            {
                "资质名称": "ISO9001质量管理体系认证证书",
                "资质等级": "AAA",
                "证书编号": "CN-001",
                "发证机关": "<ORG_CERT_1>",
                "有效期止": "2027-12-31",
            },
            ensure_ascii=False,
        ),
    )

    items = extract_template_items([material], {}, "qualification.j2", images=[image])

    assert items == [
        {
            "name": "三体系证书.pdf",
            "title": "三体系证书.pdf",
            "type": "text",
            "description": "三体系证书.pdf",
            "cert_name": "ISO9001质量管理体系认证证书",
            "cert_level": "AAA",
            "cert_number": "CN-001",
            "issuer": "<ORG_CERT_1>",
            "valid_until": "2027-12-31",
        }
    ]


def test_qualification_template_appends_matching_certificate_images(monkeypatch: pytest.MonkeyPatch) -> None:
    """模板短路路径也必须保留证书扫描图片插入契约。"""
    from app.core.writer import template_router

    monkeypatch.setattr(template_router, "should_use_template", lambda project_id: True)
    node = SimpleNamespace(title="质量认证证书")
    material = SimpleNamespace(id=697, title="三体系证书.pdf", material_type="text")
    image = SimpleNamespace(id=735, material_id=697, caption="ISO认证证书扫描件")

    class _Composer:
        def _extract_template_items(self, materials, chunk_samples, template_name, *, images=None):
            return [{"cert_name": "ISO9001质量管理体系认证证书"}]

        def _dedupe_ids(self, ids):
            return [item for item in ids if item is not None]

        def _append_figure_tags(self, html, images):
            return html + "".join(
                f'<figure data-image-id="{img.id}"><figcaption>{img.caption}</figcaption></figure>'
                for img in images
            )

    payload = try_data_fill_template(
        _Composer(),
        node,
        project_id=1562,
        materials=[material],
        chunk_samples={},
        clauses=[],
        images=[image],
    )

    assert payload is not None
    assert "<table>" in payload.content
    assert 'data-image-id="735"' in payload.content
    assert payload.source_material_ids == [697]
