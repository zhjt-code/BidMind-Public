"""招标要求提取状态接口防御测试。

职责: 锁定 get_extract_status 的完成态判定，避免普通要求已落库但评分项追加阶段仍在运行时提前返回 completed。
不做: 不启动真实后台任务、不访问真实数据库；仅用最小 fake 覆盖状态机分支。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
os.environ.setdefault("LOG_LEVEL", "DEBUG")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.tasks import TaskLiveness  # noqa: E402
from app.core.tasks.extract_task import normalize_extract_heartbeat_stage  # noqa: E402
from app.models.project import ProjectStatus  # noqa: E402
from app.services import requirement_service as requirement_service_module  # noqa: E402
from app.services.liveness import extract_liveness as extract_liveness_module  # noqa: E402
from app.services.requirement_service import RequirementService  # noqa: E402


class _ScalarResult:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar(self) -> int:
        return self._value


class _FakeDb:
    def __init__(self, existing_count: int) -> None:
        self.existing_count = existing_count
        self.committed = 0

    async def execute(self, _stmt):  # noqa: ANN001
        return _ScalarResult(self.existing_count)

    async def commit(self) -> None:
        self.committed += 1

    async def rollback(self) -> None:
        return None


def _service(
    existing_count: int,
    *,
    project: SimpleNamespace | None = None,
) -> RequirementService:
    # 完成态判定读 project.status(业务终态背书), fake 必须带该字段——
    # 生产 Project 模型恒有 status, 用 getattr 兜底只会掩盖对象异常。
    svc = RequirementService(_FakeDb(existing_count))  # type: ignore[arg-type]
    svc._get_project_or_raise = _async_return(  # type: ignore[method-assign]
        project or SimpleNamespace(id=1505, status=ProjectStatus.EXTRACTED)
    )
    svc._get_tender_doc = _async_return(SimpleNamespace(id=1303))  # type: ignore[method-assign]
    return svc


def _patch_task_state(monkeypatch, *, active: bool, heartbeat: dict | None) -> None:
    """统一桩住任务名与 inspect_extract_task_liveness, 避免每个用例重复拼 TaskLiveness。"""
    monkeypatch.setattr(
        requirement_service_module,
        "get_extract_task_name",
        lambda project_id: f"extract_requirements_{project_id}",
    )
    monkeypatch.setattr(requirement_service_module, "is_task_active", lambda _name: active)
    stage = str((heartbeat or {}).get("stage") or "")
    stage_key = stage.strip().lower()
    if heartbeat and stage_key in extract_liveness_module.EXTRACT_TERMINAL_STAGES:
        state = "terminal"
        reason = stage_key
    elif active:
        state = "live"
        reason = "fresh_heartbeat"
    elif heartbeat is None:
        state = "missing"
        reason = "no_heartbeat_or_task"
    else:
        state = "stale"
        reason = "heartbeat_stale"
    liveness = TaskLiveness(
        task_name="extract_project_1505",
        state=state,
        active=state in {"live", "enqueue_grace"},
        heartbeat=heartbeat,
        stage=stage,
        age_seconds=10.0 if active else (None if heartbeat is None else 700.0),
        reason=reason,
    )
    monkeypatch.setattr(
        requirement_service_module,
        "inspect_extract_task_liveness",
        lambda _name: liveness,
    )


def _async_return(value):  # noqa: ANN001
    async def _inner(*_args, **_kwargs):  # noqa: ANN002, ANN003
        return value

    return _inner


@pytest.mark.asyncio
async def test_extract_status_existing_requirements_but_task_active_stays_running(monkeypatch):
    """: 普通要求已落库但评分项追加仍在跑时，状态接口不得提前 completed。"""
    monkeypatch.setattr(
        requirement_service_module,
        "get_extract_task_name",
        lambda project_id: f"extract_requirements_{project_id}",
    )
    monkeypatch.setattr(
        requirement_service_module,
        "inspect_extract_task_liveness",
        lambda _name: TaskLiveness(
            task_name="extract_project_1505",
            state="live",
            active=True,
            heartbeat={"stage": "scoring_extract", "progress": "85%|评分项提取中"},
            stage="scoring_extract",
            age_seconds=10.0,
            reason="fresh_heartbeat",
        ),
    )

    result = await _service(existing_count=156).get_extract_status(
        project_id=1505,
        user=SimpleNamespace(id=1),
    )

    assert result["status"] == "running"
    assert result["stage"] == "scoring_extract"
    assert result["progress"] == 85
    assert "评分项提取中" in result["message"]


@pytest.mark.asyncio
async def test_extract_status_existing_requirements_and_no_active_task_completed(monkeypatch):
    """历史已完成项目仍可在无活跃任务时直接返回 completed。"""
    _patch_task_state(monkeypatch, active=False, heartbeat=None)

    result = await _service(existing_count=156).get_extract_status(
        project_id=1505,
        user=SimpleNamespace(id=1),
    )

    assert result["status"] == "completed"
    assert result["stage"] == "completed"


@pytest.mark.asyncio
async def test_extract_status_failed_project_without_heartbeat_returns_failed(monkeypatch):
    """: FAILED 项目即使心跳过期且 0 条要求，也不得降级成 idle 自动重启。"""
    _patch_task_state(monkeypatch, active=False, heartbeat=None)

    project = SimpleNamespace(
        id=1505,
        status=ProjectStatus.FAILED,
        error_message="提取任务被终止",
    )

    result = await _service(existing_count=0, project=project).get_extract_status(
        project_id=1505,
        user=SimpleNamespace(id=1),
    )

    assert result["status"] == "failed"
    assert result["stage"] == "failed"
    assert result["error_message"] == "提取任务被终止"


def test_extract_task_does_not_forward_child_completed_as_terminal_stage():
    """: 主提取器 completed 只是子阶段完成，不能写成任务终态心跳。

 钳制值取"合并与保存"步区间上界前一格(90-1=89)，使该步保持 running；
 若钳到 >= 90，评分项步会被状态接口判成已完成（步骤契约见 extract_progress）。
 """
    stage, progress = normalize_extract_heartbeat_stage("completed", 100)

    assert stage == "saving"
    assert progress == 89


# =====: 完成态只由业务落库终态背书，心跳过期不得宣告完成 =====


@pytest.mark.asyncio
async def test_bug757_stale_heartbeat_midstage_must_not_report_completed(monkeypatch):
    """核心防回归（doc1923 实测形态， 收口）。

 评分项阶段心跳过期且执行体未确证在途时，不得宣告 completed，也不得回 idle。
 真死必须 failed，让前端进入重试而不是无限轮询。
 """
    _patch_task_state(
        monkeypatch,
        active=False,
        heartbeat={"stage": "scoring_extract", "progress": "88%|评分项提取中"},
    )
    project = SimpleNamespace(id=1505, status=ProjectStatus.UPLOADING, error_message=None)

    result = await _service(existing_count=117, project=project).get_extract_status(
        project_id=1505,
        user=SimpleNamespace(id=1),
    )

    assert result["status"] == "failed"
    assert result["status"] != "completed"
    assert result["status"] != "idle"
    assert project.status == ProjectStatus.FAILED


@pytest.mark.asyncio
async def test_bug757_legacy_saving_heartbeat_still_reports_running(monkeypatch):
    """存量非终态心跳不得据此宣告 completed；执行体未确证在途时收敛为 failed。"""
    _patch_task_state(
        monkeypatch,
        active=False,
        heartbeat={"stage": "saving", "progress": "99%|评分项提取中"},
    )
    project = SimpleNamespace(id=1505, status=ProjectStatus.UPLOADING, error_message=None)

    result = await _service(existing_count=117, project=project).get_extract_status(
        project_id=1505,
        user=SimpleNamespace(id=1),
    )

    assert result["status"] == "failed"
    assert result["status"] != "completed"
    assert result["status"] != "idle"


@pytest.mark.asyncio
async def test_bug757_settled_business_status_reports_completed(monkeypatch):
    """业务终态已达成(EXTRACTED)即为完成，即使心跳残留在中间阶段。

    extract_task 走完全部阶段(含评分项/归并/警告收集)才提交 EXTRACTED，
    故它是"已完整落库"的权威证据，优先于任何心跳形态。
    """
    _patch_task_state(
        monkeypatch,
        active=False,
        heartbeat={"stage": "scoring_extract", "progress": "88%|评分项提取中"},
    )
    project = SimpleNamespace(id=1505, status=ProjectStatus.EXTRACTED, error_message=None)

    result = await _service(existing_count=117, project=project).get_extract_status(
        project_id=1505,
        user=SimpleNamespace(id=1),
    )

    assert result["status"] == "completed"
    assert result["progress"] == 100


@pytest.mark.asyncio
async def test_bug902_extracting_with_stale_completed_heartbeat_stays_running(monkeypatch):
    """: 重新提取已把项目打成 EXTRACTING 时，残留 completed 心跳+旧评分项不得宣告完成。"""
    _patch_task_state(
        monkeypatch,
        active=False,
        heartbeat={"stage": "completed", "progress": "100%|完成，共12条", "run_id": "old-run"},
    )
    project = SimpleNamespace(id=1505, status=ProjectStatus.EXTRACTING, error_message=None)

    result = await _service(existing_count=12, project=project).get_extract_status(
        project_id=1505,
        user=SimpleNamespace(id=1),
    )

    assert result["status"] == "running"
    assert result["status"] != "idle"
    assert result["status"] != "completed"


@pytest.mark.asyncio
async def test_bug757_terminal_heartbeat_reports_completed(monkeypatch):
    """心跳已落终态(completed)时照常完成——不因业务状态未推进而卡住。"""
    _patch_task_state(
        monkeypatch,
        active=False,
        heartbeat={"stage": "completed", "progress": "100%|完成，共117条"},
    )
    project = SimpleNamespace(id=1505, status=ProjectStatus.UPLOADING, error_message=None)

    result = await _service(existing_count=117, project=project).get_extract_status(
        project_id=1505,
        user=SimpleNamespace(id=1),
    )

    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_bug757_missing_heartbeat_keeps_legacy_completed(monkeypatch):
    """心跳已过 TTL 清理的历史项目必须保持原有 completed 行为（零回归）。"""
    _patch_task_state(monkeypatch, active=False, heartbeat=None)
    project = SimpleNamespace(id=1505, status=ProjectStatus.UPLOADING, error_message=None)

    result = await _service(existing_count=156, project=project).get_extract_status(
        project_id=1505,
        user=SimpleNamespace(id=1),
    )

    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_bug757_failure_signal_wins_over_stalled_midstage(monkeypatch):
    """真失败必须收敛为 failed，不得被"按运行中上报"分支吞掉而无限轮询。"""
    _patch_task_state(
        monkeypatch,
        active=False,
        heartbeat={"stage": "scoring_extract", "progress": "88%|评分项提取中"},
    )
    project = SimpleNamespace(
        id=1505, status=ProjectStatus.FAILED, error_message="提取任务被终止",
    )

    result = await _service(existing_count=117, project=project).get_extract_status(
        project_id=1505,
        user=SimpleNamespace(id=1),
    )

    assert result["status"] == "failed"
    assert result["error_message"] == "提取任务被终止"


@pytest.mark.asyncio
async def test_bug757_failed_heartbeat_stage_wins_over_stalled_midstage(monkeypatch):
    """失败心跳("失败"阶段)同样优先于 stalled 分支。"""
    _patch_task_state(
        monkeypatch,
        active=False,
        heartbeat={"stage": "失败", "progress": "错误: LLM 调用超时"},
    )
    project = SimpleNamespace(id=1505, status=ProjectStatus.UPLOADING, error_message=None)

    result = await _service(existing_count=117, project=project).get_extract_status(
        project_id=1505,
        user=SimpleNamespace(id=1),
    )

    assert result["status"] == "failed"
    assert "LLM 调用超时" in result["message"]


def _liveness(
    *,
    state: str,
    stage: str,
    reason: str,
    heartbeat: dict | None,
    age_seconds: float | None = 700.0,
) -> TaskLiveness:
    return TaskLiveness(
        task_name="extract_project_1528",
        state=state,
        active=state in {"live", "enqueue_grace"},
        heartbeat=heartbeat,
        stage=stage,
        age_seconds=age_seconds,
        reason=reason,
    )


def _patch_extract_liveness(monkeypatch, liveness: TaskLiveness) -> None:
    monkeypatch.setattr(
        requirement_service_module,
        "get_extract_task_name",
        lambda project_id: f"extract_project_{project_id}",
    )
    monkeypatch.setattr(
        extract_liveness_module,
        "inspect_extract_task_liveness",
        lambda _name: liveness,
    )
    monkeypatch.setattr(
        requirement_service_module,
        "inspect_extract_task_liveness",
        lambda _name: liveness,
        raising=False,
    )


@pytest.mark.parametrize(
    "stage,progress",
    [
        ("mandatory_coverage", "52%|核验否决与资格条款"),
        ("scoring_extract", "88%|评分项提取中"),
    ],
)
@pytest.mark.asyncio
async def test_bug1020_stale_midstage_extracting_reports_failed(
    monkeypatch, stage: str, progress: str
):
    """真死必须 failed 且落 FAILED，禁止再假报 running / idle / completed。"""
    heartbeat = {"stage": stage, "progress": progress}
    _patch_extract_liveness(
        monkeypatch,
        _liveness(state="stale", stage=stage, reason="heartbeat_stale", heartbeat=heartbeat),
    )
    project = SimpleNamespace(
        id=1528, status=ProjectStatus.EXTRACTING, error_message=None
    )
    svc = _service(existing_count=0, project=project)

    result = await svc.get_extract_status(project_id=1528, user=SimpleNamespace(id=1))

    assert result["status"] == "failed"
    assert result["status"] != "running"
    assert result["status"] != "idle"
    assert result["status"] != "completed"
    assert project.status == ProjectStatus.FAILED
    assert result["error_message"]


@pytest.mark.asyncio
async def test_bug1020_arq_pending_stale_age_stays_running(monkeypatch):
    """队列确证在途时即使心跳龄超过阈值，仍报 running，不得写 FAILED。"""
    heartbeat = {"stage": "mandatory_coverage", "progress": "52%|核验否决与资格条款"}
    _patch_extract_liveness(
        monkeypatch,
        _liveness(
            state="live",
            stage="mandatory_coverage",
            reason="arq_job_pending",
            heartbeat=heartbeat,
            age_seconds=900.0,
        ),
    )
    project = SimpleNamespace(
        id=1528, status=ProjectStatus.EXTRACTING, error_message=None
    )

    result = await _service(existing_count=0, project=project).get_extract_status(
        project_id=1528,
        user=SimpleNamespace(id=1),
    )

    assert result["status"] == "running"
    assert result["stage"] == "mandatory_coverage"
    assert project.status == ProjectStatus.EXTRACTING


@pytest.mark.asyncio
async def test_bug1020_stale_extracting_with_saved_rows_still_failed(monkeypatch):
    """落库条数不是执行体证据：EXTRACTING + stale 即使已有行也必须 failed。"""
    heartbeat = {"stage": "mandatory_coverage", "progress": "52%|核验否决与资格条款"}
    _patch_extract_liveness(
        monkeypatch,
        _liveness(
            state="stale",
            stage="mandatory_coverage",
            reason="heartbeat_stale",
            heartbeat=heartbeat,
        ),
    )
    project = SimpleNamespace(
        id=1528, status=ProjectStatus.EXTRACTING, error_message=None
    )

    result = await _service(existing_count=17, project=project).get_extract_status(
        project_id=1528,
        user=SimpleNamespace(id=1),
    )

    assert result["status"] == "failed"
    assert project.status == ProjectStatus.FAILED

