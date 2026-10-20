"""图示物化器与灰度开关测试（步骤3）。"""

from pathlib import Path

import pytest


class FakeDiagramRepository:
    def __init__(self, existing: dict[str, object] | None = None) -> None:
        self.existing = existing or {}
        self.project_ready: dict[int, object] = {}
        self.saved_ready: list[dict] = []
        self.saved_failed: list[dict] = []
        self._next_id = 100

    async def get_by_plan_visual(self, section_plan_id: int, visual_id: str):
        return self.existing.get(visual_id)

    async def get_ready_by_project(self, project_id: int) -> dict[int, object]:
        return self.project_ready

    async def save_ready_diagram(self, **kwargs) -> int:
        self._next_id += 1
        self.saved_ready.append({**kwargs, "id": self._next_id})
        return self._next_id

    async def save_failed_diagram(self, **kwargs) -> int:
        self._next_id += 1
        self.saved_failed.append({**kwargs, "id": self._next_id})
        return self._next_id


def _plan_payload_with_visuals() -> dict:
    return {
        "section_goal": "阐明施工组织方案",
        "measures": [
            {
                "measure_id": "measure_1",
                "action": "组建现场施工管理团队",
                "steps": ["确定项目经理", "配置专职安全员", "明确岗位职责"],
                "verification": "查验任命文件",
                "argument_ids": [],
                "project_fact_ids": [],
            },
            {
                "measure_id": "measure_2",
                "action": "编制施工进度计划",
                "steps": ["分解工序", "排布关键线路"],
                "verification": "查验进度计划表",
                "argument_ids": [],
                "project_fact_ids": [],
            },
        ],
        "visuals": [
            {
                "visual_id": "vis_1",
                "diagram_type": "flowchart",
                "title": "施工管理组织流程图",
                "purpose": "展示组建流程",
                "measure_ids": ["measure_1"],
                "placement_hint": None,
            },
            {
                "visual_id": "vis_2",
                "diagram_type": "gantt",
                "title": "进度甘特图",
                "purpose": "展示进度",
                # 无结构化 phases 的 gantt 意图 → 编译必失败
                "measure_ids": ["measure_1", "measure_2"],
                "placement_hint": None,
            },
        ],
    }


@pytest.mark.asyncio
async def test_materialize_renders_ready_and_records_failed(tmp_path: Path) -> None:
    from app.core.writer.visuals.materializer import materialize_section_diagrams

    repo = FakeDiagramRepository()
    assets = await materialize_section_diagrams(
        repo,
        project_id=1,
        outline_node_id=2,
        section_plan_id=3,
        plan_payload=_plan_payload_with_visuals(),
        output_dir=tmp_path,
    )

    # vis_1 flowchart 成功; vis_2 gantt 缺结构化 phases 失败
    assert len(assets) == 1
    asset = assets[0]
    assert asset.visual_id == "vis_1"
    assert asset.diagram_type == "flowchart"
    assert Path(asset.png_path).exists()
    assert len(repo.saved_ready) == 1
    assert len(repo.saved_failed) == 1
    assert repo.saved_failed[0]["failure_code"] == "gantt_missing_phases"


@pytest.mark.asyncio
async def test_materialize_reuses_existing_ready_asset(tmp_path: Path) -> None:
    from app.core.writer.visuals.materializer import materialize_section_diagrams

    png = tmp_path / "existing.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    class ExistingRow:
        id = 55
        status = "ready"
        png_path = str(png)
        diagram_type = "flowchart"
        title = "施工管理组织流程图"

    payload = _plan_payload_with_visuals()
    payload["visuals"] = payload["visuals"][:1]
    repo = FakeDiagramRepository(existing={"vis_1": ExistingRow()})
    assets = await materialize_section_diagrams(
        repo,
        project_id=1,
        outline_node_id=2,
        section_plan_id=3,
        plan_payload=payload,
        output_dir=tmp_path,
    )
    assert len(assets) == 1
    assert assets[0].diagram_id == 55
    assert repo.saved_ready == []


@pytest.mark.asyncio
async def test_materialize_respects_max_per_section(tmp_path: Path, monkeypatch) -> None:
    from app.core.writer.section_composer import settings
    from app.core.writer.visuals.materializer import materialize_section_diagrams

    monkeypatch.setattr(settings, "WRITING_SECTION_DIAGRAM_MAX_PER_SECTION", 1)
    payload = _plan_payload_with_visuals()
    # 两个都可渲染的 flowchart
    payload["visuals"][1] = {
        "visual_id": "vis_2",
        "diagram_type": "flowchart",
        "title": "第二张流程图",
        "purpose": "展示流程",
        "measure_ids": ["measure_1"],
        "placement_hint": None,
    }
    repo = FakeDiagramRepository()
    assets = await materialize_section_diagrams(
        repo,
        project_id=1,
        outline_node_id=2,
        section_plan_id=3,
        plan_payload=payload,
        output_dir=tmp_path,
    )
    assert len(assets) == 1


def test_diagram_rollout_flag_off(monkeypatch) -> None:
    from app.core.config import settings
    from app.core.writer.visuals.feature_flags import decide_section_diagram_rollout

    monkeypatch.setattr(settings, "WRITING_SECTION_DIAGRAM_ENABLED", False)
    decision = decide_section_diagram_rollout(123)
    assert decision.enabled is False
    assert decision.reason == "diagram_flag_off"


def test_diagram_rollout_full_ratio(monkeypatch) -> None:
    from app.core.config import settings
    from app.core.writer.visuals.feature_flags import decide_section_diagram_rollout

    monkeypatch.setattr(settings, "WRITING_SECTION_DIAGRAM_ENABLED", True)
    monkeypatch.setattr(settings, "WRITING_SECTION_DIAGRAM_RATIO", 1.0)
    decision = decide_section_diagram_rollout(123)
    assert decision.enabled is True


@pytest.mark.asyncio
async def test_materialize_respects_project_total_budget(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from app.core.config import settings
    from app.core.writer.visuals.materializer import materialize_section_diagrams

    class ExistingRow:
        id = 1
        status = "ready"
        title = "既有图示"
        ir_json = {}

    monkeypatch.setattr(settings, "WRITING_SECTION_DIAGRAM_DOC_MAX_TOTAL", 1)
    payload = _plan_payload_with_visuals()
    payload["visuals"] = payload["visuals"][:1]
    repo = FakeDiagramRepository()
    repo.project_ready = {1: ExistingRow()}

    assets = await materialize_section_diagrams(
        repo,
        project_id=1,
        outline_node_id=2,
        section_plan_id=3,
        plan_payload=payload,
        output_dir=tmp_path,
    )

    assert assets == []
    assert repo.saved_failed[0]["failure_code"] == "diagram_budget_exceeded"


@pytest.mark.asyncio
async def test_materialize_skips_duplicate_existing_title(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from app.core.config import settings
    from app.core.writer.visuals.materializer import materialize_section_diagrams

    class ExistingRow:
        id = 1
        status = "ready"
        title = "施工管理组织流程图"
        ir_json = {}

    monkeypatch.setattr(settings, "WRITING_SECTION_DIAGRAM_DOC_MAX_TOTAL", 10)
    payload = _plan_payload_with_visuals()
    payload["visuals"] = payload["visuals"][:1]
    repo = FakeDiagramRepository()
    repo.project_ready = {1: ExistingRow()}

    assets = await materialize_section_diagrams(
        repo,
        project_id=1,
        outline_node_id=2,
        section_plan_id=3,
        plan_payload=payload,
        output_dir=tmp_path,
    )

    assert assets == []
    assert repo.saved_failed[0]["failure_code"] == "diagram_duplicate"
