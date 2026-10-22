"""VisualIntent 物化器: 计划意图 → PNG 资产 + DB 记录。

职责: 编译通过闸门的 VisualIntent, 渲染 PNG 并落库, 输出可消费资产清单。
不做: 不调用 LLM、不修改章节正文、不写 Word。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from app.core.config import settings
from app.core.writer.visuals.ir_builder import DiagramBuildError, build_diagram_ir
from app.core.writer.visuals.png_renderer import render_diagram_png

_DIAGRAM_TYPE_LABELS = {
    "flowchart": "流程图",
    "gantt": "甘特图",
    "swimlane": "泳道图",
    "roadmap": "路线图",
    "org_chart": "组织架构图",
    "layered": "分层体系图",
    "network": "进度网络图",
    "wbs": "工作分解图",
    "zone_layout": "场地分区示意图",
}


@dataclass(frozen=True)
class DiagramAsset:
    """可供正文 figure 协议消费的已渲染图示。"""

    diagram_id: int
    visual_id: str
    diagram_type: str
    title: str
    png_path: str


def _normalize_title(value: str) -> str:
    return re.sub(r"[\s《》:：图表\-—_]+", "", value or "").lower()


async def _load_project_ready_diagrams(
    repository: Any,
    project_id: int,
) -> dict[int, object]:
    loader = getattr(repository, "get_ready_by_project", None)
    if loader is None:
        return {}
    return await loader(project_id)


async def _save_skipped_diagram(
    repository: Any,
    *,
    project_id: int,
    outline_node_id: int,
    section_plan_id: int,
    intent: dict[str, Any],
    failure_code: str,
) -> None:
    await repository.save_failed_diagram(
        project_id=project_id,
        outline_node_id=outline_node_id,
        section_plan_id=section_plan_id,
        visual_id=str(intent.get("visual_id", "")),
        diagram_type=str(intent.get("diagram_type", "")),
        title=str(intent.get("title", "")),
        failure_code=failure_code,
    )


def _diagram_output_dir(project_id: int, outline_node_id: int) -> Path:
    return (
        Path(settings.UPLOAD_DIR)
        / "writing_diagrams"
        / str(project_id)
        / str(outline_node_id)
    )


async def materialize_section_diagrams(
    repository: Any,
    *,
    project_id: int,
    outline_node_id: int,
    section_plan_id: int,
    plan_payload: dict[str, Any],
    output_dir: Path | None = None,
) -> list[DiagramAsset]:
    """物化计划中的图示意图; 单图失败只记录不阻断。"""

    visuals = list(plan_payload.get("visuals") or [])
    max_per_section = int(settings.WRITING_SECTION_DIAGRAM_MAX_PER_SECTION)
    if len(visuals) > max_per_section:
        logger.info(
            "图示数量超过上限截断: node={}, requested={}, max={}",
            outline_node_id, len(visuals), max_per_section,
        )
        visuals = visuals[:max_per_section]
    if not visuals:
        return []

    base_dir = output_dir or _diagram_output_dir(project_id, outline_node_id)
    project_ready = await _load_project_ready_diagrams(repository, project_id)
    ready_titles = {
        _normalize_title(str(getattr(record, "title", "") or ""))
        for record in project_ready.values()
        if str(getattr(record, "title", "") or "").strip()
    }
    doc_max_total = int(settings.WRITING_SECTION_DIAGRAM_DOC_MAX_TOTAL)
    remaining_new_budget = max(doc_max_total - len(project_ready), 0)
    assets: list[DiagramAsset] = []
    for intent in visuals:
        visual_id = str(intent.get("visual_id", ""))
        diagram_type = str(intent.get("diagram_type", ""))
        title = str(intent.get("title", ""))
        try:
            existing = await repository.get_by_plan_visual(section_plan_id, visual_id)
            if (
                existing is not None
                and existing.status == "ready"
                and existing.png_path
                and Path(existing.png_path).exists()
            ):
                assets.append(DiagramAsset(
                    diagram_id=int(existing.id),
                    visual_id=visual_id,
                    diagram_type=str(existing.diagram_type),
                    title=str(existing.title),
                    png_path=str(existing.png_path),
                ))
                continue

            if remaining_new_budget <= 0:
                logger.info(
                    "图示项目总量预算已满: project={}, visual={}, max={}",
                    project_id,
                    visual_id,
                    doc_max_total,
                )
                await _save_skipped_diagram(
                    repository,
                    project_id=project_id,
                    outline_node_id=outline_node_id,
                    section_plan_id=section_plan_id,
                    intent=intent,
                    failure_code="diagram_budget_exceeded",
                )
                continue
            normalized_title = _normalize_title(title)
            if normalized_title and normalized_title in ready_titles:
                logger.info(
                    "图示重复标题跳过: project={}, visual={}, title={}",
                    project_id,
                    visual_id,
                    title,
                )
                await _save_skipped_diagram(
                    repository,
                    project_id=project_id,
                    outline_node_id=outline_node_id,
                    section_plan_id=section_plan_id,
                    intent=intent,
                    failure_code="diagram_duplicate",
                )
                continue

            ir = build_diagram_ir(plan_payload, intent)
            png_path = base_dir / f"{visual_id}_{ir.content_sha256[:8]}.png"
            render_diagram_png(ir, png_path)
            diagram_id = await repository.save_ready_diagram(
                project_id=project_id,
                outline_node_id=outline_node_id,
                section_plan_id=section_plan_id,
                visual_id=visual_id,
                diagram_type=ir.diagram_type,
                title=title,
                ir_json=ir.model_dump(mode="json"),
                png_path=str(png_path),
                content_sha256=ir.content_sha256,
            )
            assets.append(DiagramAsset(
                diagram_id=diagram_id,
                visual_id=visual_id,
                diagram_type=ir.diagram_type,
                title=title,
                png_path=str(png_path),
            ))
            if normalized_title:
                ready_titles.add(normalized_title)
            remaining_new_budget -= 1
        except DiagramBuildError as exc:
            logger.info(
                "图示编译失败降级: node={}, visual={}, code={}",
                outline_node_id, visual_id, exc.failure_code,
            )
            await repository.save_failed_diagram(
                project_id=project_id,
                outline_node_id=outline_node_id,
                section_plan_id=section_plan_id,
                visual_id=visual_id,
                diagram_type=diagram_type,
                title=title,
                failure_code=exc.failure_code,
            )
        except Exception as exc:  # noqa: BLE001 - 单图失败不阻断章节生成
            logger.warning(
                "图示渲染失败降级: node={}, visual={}, err={!r}",
                outline_node_id, visual_id, exc,
            )
            await repository.save_failed_diagram(
                project_id=project_id,
                outline_node_id=outline_node_id,
                section_plan_id=section_plan_id,
                visual_id=visual_id,
                diagram_type=diagram_type,
                title=title,
                failure_code="render_failed",
            )
    return assets


def render_diagram_directives(assets: list[DiagramAsset]) -> str:
    """构造进入 free_writing Prompt 的图示放置指令。"""

    if not assets:
        return ""
    lines = [
        "本章已生成以下系统图示，正文必须在最相关的措施/论述段落之后原样插入对应标记"
        "（禁止改写标记、禁止虚构其他图示ID、禁止写成详见附件/附图/另附图示）:",
    ]
    for asset in assets:
        type_label = _DIAGRAM_TYPE_LABELS.get(asset.diagram_type, asset.diagram_type)
        lines.append(
            f'- {type_label}《{asset.title}》: '
            f'<figure data-diagram-id="{asset.diagram_id}">'
            f"<figcaption>{asset.title}</figcaption></figure>"
        )
    return "\n".join(lines)


__all__ = [
    "DiagramAsset",
    "materialize_section_diagrams",
    "render_diagram_directives",
]
