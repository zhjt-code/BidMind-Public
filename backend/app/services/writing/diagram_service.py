"""章节生成图示编辑服务。

职责: 读取章节生成图示并根据人工编辑重绘 PNG。
不做: 不生成 VisualIntent、不修改章节草稿正文、不渲染 Word。
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import NotFoundError, ServiceError
from app.core.writer.visuals.ir_builder import DiagramBuildError, build_diagram_ir
from app.core.writer.visuals.png_renderer import render_diagram_png
from app.core.writer.visuals.repository import SectionDiagramRepository
from app.schemas.writing_diagram import (
    WritingDiagramResponse,
    WritingDiagramUpdateRequest,
)

RenderInvalidator = Callable[[int], Awaitable[None] | None]


def _definition_from_ir(diagram_type: str, ir_json: dict) -> dict:
    """把 IR JSON 还原为编辑态 definition。"""

    if diagram_type == "gantt":
        return {
            "phases": [
                {
                    "label": str(bar.get("label", "")),
                    "duration_days": int(bar.get("duration_days", 0)),
                }
                for bar in ir_json.get("bars", [])
            ],
        }
    if diagram_type == "flowchart":
        return {
            "node_labels": [
                str(node.get("label", ""))
                for node in ir_json.get("nodes", [])
            ],
        }
    if diagram_type == "org_chart":
        return {
            "org_lead": str(ir_json.get("lead", "")),
            "org_units": [str(item) for item in ir_json.get("units", [])],
        }
    if diagram_type == "swimlane":
        return {
            "lanes": [str(item) for item in ir_json.get("lanes", [])],
            "swim_nodes": [
                {
                    "label": str(node.get("label", "")),
                    "lane": str(node.get("lane", "")),
                }
                for node in ir_json.get("nodes", [])
            ],
        }
    if diagram_type == "roadmap":
        return {
            "phases": [
                {
                    "label": str(phase.get("label", "")),
                    "duration_days": int(phase.get("duration_days", 0)),
                    "milestone": bool(phase.get("milestone")),
                }
                for phase in ir_json.get("phases", [])
            ],
        }
    if diagram_type == "layered":
        return {"layers": list(ir_json.get("layers", []) or [])}
    if diagram_type == "network":
        return {
            "network_nodes": [
                {
                    "node_id": str(node.get("node_id", "")),
                    "label": str(node.get("label", "")),
                    "duration_days": int(node.get("duration_days", 0)),
                }
                for node in ir_json.get("nodes", [])
            ],
            "network_edges": [
                {
                    "source": str(edge.get("source", "")),
                    "target": str(edge.get("target", "")),
                }
                for edge in ir_json.get("edges", [])
            ],
        }
    if diagram_type == "wbs":
        return {
            "wbs_root": str(ir_json.get("root", "")),
            "wbs_packages": list(ir_json.get("packages", []) or []),
        }
    if diagram_type == "zone_layout":
        return {"zones": [str(item) for item in ir_json.get("zones", [])]}
    raise ServiceError(f"不支持的图示类型: {diagram_type}")


class WritingSectionDiagramService:
    """章节图示人工编辑与重绘服务。"""

    def __init__(
        self,
        db: AsyncSession | None,
        *,
        render_invalidator: RenderInvalidator,
        repository: SectionDiagramRepository | None = None,
        output_dir: Path | None = None,
    ) -> None:
        self.db = db
        self._render_invalidator = render_invalidator
        self._repository = repository or SectionDiagramRepository(db)  # type: ignore[arg-type]
        self._output_dir = output_dir

    async def get_diagram_for_editing(
        self,
        *,
        project_id: int,
        diagram_id: int,
    ) -> WritingDiagramResponse:
        """读取编辑态图示定义。"""

        record = await self._get_owned_diagram(project_id, diagram_id)
        return self._to_response(record)

    async def resolve_diagram_png_path(
        self,
        *,
        project_id: int,
        diagram_id: int,
    ) -> Path:
        """解析图示 PNG 文件路径，并校验归属与就绪状态。"""

        record = await self._get_owned_diagram(project_id, diagram_id)
        if getattr(record, "status", "") != "ready":
            raise NotFoundError("图示尚未生成")
        png_path = Path(str(getattr(record, "png_path", "") or ""))
        if not png_path.exists():
            raise NotFoundError("图示文件不存在")
        return png_path

    async def apply_diagram_edit(
        self,
        *,
        project_id: int,
        diagram_id: int,
        body: WritingDiagramUpdateRequest,
    ) -> WritingDiagramResponse:
        """应用人工编辑并重绘 PNG。"""

        record = await self._get_owned_diagram(project_id, diagram_id)
        intent = self._build_edit_intent(record, body)
        try:
            ir = build_diagram_ir({}, intent)
        except DiagramBuildError as exc:
            raise ServiceError(f"图表参数不合法: {exc.failure_code}") from exc

        png_path = self._output_path(record, ir.content_sha256)
        render_diagram_png(ir, png_path)

        record.title = ir.title
        record.diagram_type = ir.diagram_type
        record.ir_json = ir.model_dump(mode="json")
        record.png_path = str(png_path)
        record.content_sha256 = ir.content_sha256
        record.status = "ready"
        record.failure_code = None

        await self._maybe_commit()
        await self._maybe_invalidate(project_id)
        return self._to_response(record)

    async def _get_owned_diagram(self, project_id: int, diagram_id: int):
        record = await self._repository.get_by_project_diagram(project_id, diagram_id)
        if record is None:
            raise NotFoundError("图示不存在")
        return record

    @staticmethod
    def _to_response(record: object) -> WritingDiagramResponse:
        diagram_type = str(getattr(record, "diagram_type", ""))
        ir_json = getattr(record, "ir_json", None) or {}
        definition = _definition_from_ir(diagram_type, ir_json)
        definition["style"] = ir_json.get("style") or {
            "theme": "classic_blue",
            "font_scale": "md",
        }
        return WritingDiagramResponse(
            id=int(getattr(record, "id")),
            diagram_type=diagram_type,  # type: ignore[arg-type]
            title=str(getattr(record, "title", "")),
            status=str(getattr(record, "status", "")),
            definition=definition,
            content_sha256=str(getattr(record, "content_sha256", "") or ""),
        )

    @staticmethod
    def _build_edit_intent(
        record: object,
        body: WritingDiagramUpdateRequest,
    ) -> dict:
        diagram_type = str(getattr(record, "diagram_type", ""))
        intent = {
            "visual_id": str(getattr(record, "visual_id", "manual_edit")),
            "diagram_type": diagram_type,
            "title": body.title,
            "purpose": "人工编辑图示",
            "measure_ids": ["manual_edit"],
            "placement_hint": None,
        }
        if body.style is not None:
            intent["style"] = body.style.model_dump(mode="json")
        if diagram_type in {"gantt", "roadmap"}:
            intent["phases"] = [
                phase.model_dump()
                for phase in (body.phases or [])
            ]
            return intent
        if diagram_type == "flowchart":
            intent["node_labels"] = body.node_labels or []
            return intent
        if diagram_type == "org_chart":
            intent["org_lead"] = body.org_lead or ""
            intent["org_units"] = body.org_units or []
            return intent
        if diagram_type == "swimlane":
            intent["lanes"] = body.lanes or []
            intent["swim_nodes"] = [
                node.model_dump()
                for node in (body.swim_nodes or [])
            ]
            return intent
        if diagram_type == "layered":
            intent["layers"] = [
                layer.model_dump()
                for layer in (body.layers or [])
            ]
            return intent
        if diagram_type == "network":
            intent["network_nodes"] = [
                node.model_dump()
                for node in (body.network_nodes or [])
            ]
            intent["network_edges"] = [
                edge.model_dump()
                for edge in (body.network_edges or [])
            ]
            return intent
        if diagram_type == "wbs":
            intent["wbs_root"] = body.wbs_root or ""
            intent["wbs_packages"] = [
                package.model_dump()
                for package in (body.wbs_packages or [])
            ]
            return intent
        if diagram_type == "zone_layout":
            intent["zones"] = body.zones or []
            return intent
        raise ServiceError(f"不支持的图示类型: {diagram_type}")

    def _output_path(self, record: object, content_sha256: str) -> Path:
        base_dir = self._output_dir or (
            Path(settings.UPLOAD_DIR)
            / "writing_diagrams"
            / str(getattr(record, "project_id"))
            / str(getattr(record, "outline_node_id"))
        )
        return (
            Path(base_dir)
            / f"{getattr(record, 'visual_id', 'diagram')}_{content_sha256[:8]}.png"
        )

    async def _maybe_commit(self) -> None:
        if self.db is None:
            return
        await self.db.commit()

    async def _maybe_invalidate(self, project_id: int) -> None:
        result = self._render_invalidator(project_id)
        if inspect.isawaitable(result):
            await result


__all__ = ["WritingSectionDiagramService"]
