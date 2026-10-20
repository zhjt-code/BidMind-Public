"""章节图示编辑重绘服务测试。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


def _record(
    *,
    project_id: int = 1,
    outline_node_id: int = 10,
    diagram_id: int = 7,
    diagram_type: str = "gantt",
    title: str = "工期保障甘特图",
    png_path: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=diagram_id,
        project_id=project_id,
        outline_node_id=outline_node_id,
        section_plan_id=3,
        visual_id="vis_1",
        diagram_type=diagram_type,
        title=title,
        ir_json={
            "diagram_type": "gantt",
            "title": title,
            "bars": [
                {"label": "准备", "start_day": 0, "duration_days": 10},
                {"label": "实施", "start_day": 10, "duration_days": 20},
            ],
            "content_sha256": "0" * 64,
        } if diagram_type == "gantt" else {
            "diagram_type": "flowchart",
            "title": title,
            "nodes": [
                {"node_id": "n1", "label": "启动"},
                {"node_id": "n2", "label": "执行"},
                {"node_id": "n3", "label": "验收"},
            ],
            "edges": [
                {"source": "n1", "target": "n2"},
                {"source": "n2", "target": "n3"},
            ],
            "content_sha256": "0" * 64,
        },
        png_path=png_path,
        content_sha256="0" * 64,
        status="ready",
        failure_code=None,
    )


class FakeDiagramRepository:
    def __init__(self, record: SimpleNamespace | None) -> None:
        self.record = record

    async def get_by_project_diagram(self, project_id: int, diagram_id: int):
        if self.record and self.record.project_id == project_id and self.record.id == diagram_id:
            return self.record
        return None


@pytest.mark.asyncio
async def test_get_diagram_for_editing_restores_gantt_definition(tmp_path: Path) -> None:
    from app.services.writing.diagram_service import WritingSectionDiagramService

    png = tmp_path / "old.png"
    png.write_bytes(b"old")
    service = WritingSectionDiagramService(
        db=None,
        render_invalidator=lambda _project_id: None,
        repository=FakeDiagramRepository(_record(png_path=str(png))),
        output_dir=tmp_path,
    )

    result = await service.get_diagram_for_editing(project_id=1, diagram_id=7)

    assert result.id == 7
    assert result.diagram_type == "gantt"
    assert result.content_sha256 == "0" * 64
    assert result.definition["phases"] == [
        {"label": "准备", "duration_days": 10},
        {"label": "实施", "duration_days": 20},
    ]


@pytest.mark.asyncio
async def test_get_diagram_for_editing_returns_style_definition(tmp_path: Path) -> None:
    from app.services.writing.diagram_service import WritingSectionDiagramService

    png = tmp_path / "old.png"
    png.write_bytes(b"old")
    record = _record(png_path=str(png))
    record.ir_json["style"] = {
        "theme": "business_gray",
        "primary_color": "#666666",
        "text_color": "#222222",
        "font_scale": "sm",
    }
    service = WritingSectionDiagramService(
        db=None,
        render_invalidator=lambda _project_id: None,
        repository=FakeDiagramRepository(record),
        output_dir=tmp_path,
    )

    result = await service.get_diagram_for_editing(project_id=1, diagram_id=7)

    assert result.definition["style"] == {
        "theme": "business_gray",
        "primary_color": "#666666",
        "text_color": "#222222",
        "font_scale": "sm",
    }


@pytest.mark.asyncio
async def test_apply_gantt_edit_rerenders_png_and_invalidates_artifact(tmp_path: Path) -> None:
    from app.schemas.writing_diagram import WritingDiagramUpdateRequest
    from app.services.writing.diagram_service import WritingSectionDiagramService

    invalidated: list[int] = []

    async def invalidate(project_id: int) -> None:
        invalidated.append(project_id)

    record = _record(png_path=str(tmp_path / "old.png"))
    service = WritingSectionDiagramService(
        db=None,
        render_invalidator=invalidate,
        repository=FakeDiagramRepository(record),
        output_dir=tmp_path,
    )

    result = await service.apply_diagram_edit(
        project_id=1,
        diagram_id=7,
        body=WritingDiagramUpdateRequest(
            title="调整后甘特图",
            phases=[
                {"label": "准备阶段", "duration_days": 5},
                {"label": "集中实施", "duration_days": 15},
                {"label": "验收提交", "duration_days": 8},
            ],
            style={
                "theme": "tech_teal",
                "primary_color": "#0f766e",
                "text_color": "#123456",
                "font_scale": "lg",
            },
        ),
    )

    assert result.title == "调整后甘特图"
    assert result.content_sha256 == record.content_sha256
    assert len(result.content_sha256) == 64
    assert record.title == "调整后甘特图"
    assert record.status == "ready"
    assert record.failure_code is None
    assert record.ir_json["title"] == "调整后甘特图"
    assert record.ir_json["bars"] == [
        {"label": "准备阶段", "start_day": 0, "duration_days": 5},
        {"label": "集中实施", "start_day": 5, "duration_days": 15},
        {"label": "验收提交", "start_day": 20, "duration_days": 8},
    ]
    assert record.ir_json["style"]["theme"] == "tech_teal"
    assert record.ir_json["style"]["primary_color"] == "#0f766e"
    assert record.ir_json["style"]["font_scale"] == "lg"
    assert Path(record.png_path).exists()
    assert invalidated == [1]


@pytest.mark.asyncio
async def test_invalid_gantt_edit_keeps_existing_record_and_cache(tmp_path: Path) -> None:
    from app.core.exceptions import ServiceError
    from app.schemas.writing_diagram import WritingDiagramUpdateRequest
    from app.services.writing.diagram_service import WritingSectionDiagramService

    invalidated: list[int] = []
    record = _record(png_path=str(tmp_path / "old.png"))
    old_ir = dict(record.ir_json)
    service = WritingSectionDiagramService(
        db=None,
        render_invalidator=lambda project_id: invalidated.append(project_id),
        repository=FakeDiagramRepository(record),
        output_dir=tmp_path,
    )

    with pytest.raises(ServiceError):
        await service.apply_diagram_edit(
            project_id=1,
            diagram_id=7,
            body=WritingDiagramUpdateRequest(
                title="非法甘特图",
                phases=[{"label": "只有一段", "duration_days": 5}],
            ),
        )

    assert record.title == "工期保障甘特图"
    assert record.ir_json == old_ir
    assert invalidated == []


@pytest.mark.asyncio
async def test_apply_flowchart_edit_rerenders_from_node_labels(tmp_path: Path) -> None:
    from app.schemas.writing_diagram import WritingDiagramUpdateRequest
    from app.services.writing.diagram_service import WritingSectionDiagramService

    record = _record(diagram_type="flowchart", title="流程图", png_path=str(tmp_path / "old.png"))
    service = WritingSectionDiagramService(
        db=None,
        render_invalidator=lambda _project_id: None,
        repository=FakeDiagramRepository(record),
        output_dir=tmp_path,
    )

    result = await service.apply_diagram_edit(
        project_id=1,
        diagram_id=7,
        body=WritingDiagramUpdateRequest(
            title="调整后流程图",
            node_labels=["启动会", "资料准备", "过程检查", "最终验收"],
        ),
    )

    assert record.diagram_type == "flowchart"
    assert [node["label"] for node in record.ir_json["nodes"]] == [
        "启动会",
        "资料准备",
        "过程检查",
        "最终验收",
    ]
    assert record.ir_json["edges"][-1] == {"source": "n3", "target": "n4"}
    assert Path(record.png_path).exists()
    assert result.content_sha256 == record.content_sha256
    assert result.content_sha256 != "0" * 64


@pytest.mark.asyncio
async def test_apply_zone_layout_edit_rerenders(tmp_path: Path) -> None:
    from app.schemas.writing_diagram import WritingDiagramUpdateRequest
    from app.services.writing.diagram_service import WritingSectionDiagramService

    record = _record(
        diagram_type="zone_layout",
        title="场地分区",
        png_path=str(tmp_path / "old.png"),
    )
    record.ir_json = {
        "diagram_type": "zone_layout",
        "title": "场地分区（示意图，非施工平面图）",
        "zones": ["办公区", "生活区", "施工区"],
        "content_sha256": "0" * 64,
    }
    service = WritingSectionDiagramService(
        db=None,
        render_invalidator=lambda _project_id: None,
        repository=FakeDiagramRepository(record),
        output_dir=tmp_path,
    )

    result = await service.apply_diagram_edit(
        project_id=1,
        diagram_id=7,
        body=WritingDiagramUpdateRequest(
            title="场地功能分区",
            zones=["办公区", "生活区", "材料堆场", "大门"],
        ),
    )

    assert result.diagram_type == "zone_layout"
    assert "非施工平面图" in record.title
    assert record.ir_json["zones"] == ["办公区", "生活区", "材料堆场", "大门"]
    assert Path(record.png_path).exists()
    assert result.content_sha256 == record.content_sha256


def test_diagram_png_route_disables_browser_cache() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "api"
        / "routers"
        / "writing_diagrams.py"
    ).read_text(encoding="utf-8")

    assert "Cache-Control" in source
    assert "no-store" in source
    assert "FileResponse(" in source


@pytest.mark.asyncio
async def test_cross_project_diagram_access_is_rejected(tmp_path: Path) -> None:
    from app.core.exceptions import NotFoundError
    from app.services.writing.diagram_service import WritingSectionDiagramService

    service = WritingSectionDiagramService(
        db=None,
        render_invalidator=lambda _project_id: None,
        repository=FakeDiagramRepository(_record(project_id=2)),
        output_dir=tmp_path,
    )

    with pytest.raises(NotFoundError):
        await service.get_diagram_for_editing(project_id=1, diagram_id=7)
