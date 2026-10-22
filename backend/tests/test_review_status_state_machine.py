"""ReviewService.get_review_status 行为对等性防御测试基线。

目的: 在 ReviewService.get_review_status 的 540 行单方法被拆分前,
固定 15+ 状态机分支的期望输出, 确保后续 "搬家不改语义" 重构有完整回归护栏。

测试沿用 tests/test_review_task_finalization.py 的 SimpleNamespace + fake session
+ monkeypatch 风格, 不触碰真实数据库或 Redis。

覆盖分支:
 (a) total_requirements=0 -> stage=pending, message="等待启动审查"
 (b) extracted < total -> stage=extracting
 (c) extracted==total, reviewed=0 -> stage=assembling
 (d) 0 < reviewed < total -> stage=reviewing
 (e) reviewed==total, REVIEWING, task live -> stage=reviewing (收尾中)
 (f) Project.COMPLETED -> stage=completed, progress=1.0
 (g) Project.FAILED -> stage=failed
 (h) 结果满 + task 不活 -> 自动补 COMPLETED
 (i) state=stale + reviewed<total -> Project 转 FAILED (心跳超时)
 (j) state=missing + reviewed<total -> Project 转 FAILED (任务缺失)
 (k) state=terminal + recovery<MAX -> dispatch 自动恢复
 (l) state=terminal + recovery>=MAX -> 转 FAILED (恢复上限)
 (m) state=enqueue_grace -> 不触发任何恢复 (入队宽限)
 (n) 进度停滞 + 心跳 fresh -> 不重启
 (o) 进度停滞 + 心跳 stale -> 重启
 (p) report_ready 在 COMPLETED -> steps 全部 completed + progress=100
 (q) reviewed==total + self_healing -> reviewing + 第3步 running detail 含重试进度
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.review_contracts import (
    REVIEW_STAGE_ASSEMBLING,
    REVIEW_STAGE_COMPLETED,
    REVIEW_STAGE_EXTRACTING,
    REVIEW_STAGE_FAILED,
    REVIEW_STAGE_PENDING,
    REVIEW_STAGE_REVIEWING,
)
from app.core.tasks import TaskLiveness
from app.models.project import ProjectStatus
from app.models.user import UserRole
from app.services import review_service as review_service_module

# ============================================================
# 通用桩
# ============================================================


class _FakeExecResult:
    def __init__(
        self,
        *,
        scalar_value: Any = None,
        scalar_one_value: Any = None,
        scalars_value: Optional[list[Any]] = None,
    ) -> None:
        self._scalar_value = scalar_value
        self._scalar_one_value = scalar_one_value
        self._scalars_value = scalars_value or []

    def scalar(self) -> Any:
        return self._scalar_value

    def scalar_one_or_none(self) -> Any:
        return self._scalar_one_value

    def scalars(self):
        values = list(self._scalars_value)

        class _Scalars:
            def all(self_inner):
                return values

        return _Scalars()


class _FakeServiceSession:
    """get_review_status 的最小 DB 桩: 按调用顺序返回准备好的 execute 响应。"""

    def __init__(self, responses: list[_FakeExecResult]) -> None:
        self._responses = responses
        self._idx = 0
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, _stmt):
        if self._idx >= len(self._responses):
            raise AssertionError(
                f"execute() 调用次数 {self._idx + 1} 超过测试准备的响应队列长度 {len(self._responses)}"
            )
        value = self._responses[self._idx]
        self._idx += 1
        return value

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def _make_user(owner_id: int = 1, role=UserRole.ADMIN) -> SimpleNamespace:
    return SimpleNamespace(id=owner_id, role=role)


def _make_project(
    *,
    project_id: int,
    status: ProjectStatus = ProjectStatus.REVIEWING,
    error_message: Optional[str] = None,
    owner_id: int = 1,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=project_id,
        status=status,
        error_message=error_message,
        owner_id=owner_id,
        billing_mode="free",
        billing_hold_id=None,
    )


def _make_count_responses(
    *,
    project: SimpleNamespace,
    total: int,
    extracted: int,
    reviewed: int,
) -> list[_FakeExecResult]:
    """get_review_status 入口的固定 5 次 execute 顺序:
    1) _get_project_or_raise -> Project
    2) tender_doc 查询 -> Document or None
    3) requirements 查询 -> [Requirement]
    4) BidResponse count -> extracted_responses
    5) ReviewResult count -> reviewed_items
    """
    tender_doc = SimpleNamespace(id=501) if total > 0 else None
    requirements = [SimpleNamespace(id=10 + i) for i in range(total)]
    return [
        _FakeExecResult(scalar_one_value=project),
        _FakeExecResult(scalar_one_value=tender_doc),
        _FakeExecResult(scalars_value=requirements),
        _FakeExecResult(scalar_value=extracted),
        _FakeExecResult(scalar_value=reviewed),
    ]


def _make_liveness(
    state: str = "live",
    *,
    heartbeat: Optional[dict] = None,
    age_seconds: Optional[float] = None,
    reason: Optional[str] = None,
) -> TaskLiveness:
    """构造 TaskLiveness 实例. is_live = state in {live, enqueue_grace}."""
    return TaskLiveness(
        task_name="review_project_test",
        state=state,
        active=(state == "live"),
        heartbeat=heartbeat,
        stage=str((heartbeat or {}).get("stage") or "").lower(),
        age_seconds=age_seconds if age_seconds is not None else (0.0 if heartbeat else None),
        reason=reason if reason is not None else f"test_{state}",
    )


def _install_status_stubs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    liveness: Optional[TaskLiveness] = None,
    recovery_count: int = 0,
    heartbeat: Optional[dict] = None,
    terminal_snapshot: Optional[tuple] = None,
    dispatch_calls: Optional[list] = None,
    cancel_calls: Optional[list] = None,
    increment_calls: Optional[list] = None,
    reset_calls: Optional[list] = None,
    arq_inflight: bool = False,
) -> None:
    """安装 get_review_status 所需的全部外部依赖桩。"""

    if liveness is None:
        liveness = _make_liveness("live")
    monkeypatch.setattr(
        review_service_module, "inspect_task_liveness", lambda *_a, **_k: liveness
    )
    monkeypatch.setattr(review_service_module, "get_recovery_count", lambda _n: recovery_count)
    monkeypatch.setattr(review_service_module, "get_task_heartbeat", lambda _n: heartbeat)

    async def _inflight(*_a, **_k):
        return arq_inflight

    monkeypatch.setattr(
        "app.core.tasks.lifecycle.liveness_evidence.is_arq_job_possibly_inflight",
        _inflight,
    )

    def _inc(n):
        if increment_calls is not None:
            increment_calls.append(n)
        return recovery_count + 1

    monkeypatch.setattr(review_service_module, "increment_recovery_count", _inc)

    def _reset(n):
        if reset_calls is not None:
            reset_calls.append(n)

    monkeypatch.setattr(review_service_module, "reset_recovery_count", _reset)

    def _cancel(n):
        if cancel_calls is not None:
            cancel_calls.append(n)

    monkeypatch.setattr(review_service_module, "cancel_task", _cancel)

    async def _stub_recheck(_db, _pid):
        return terminal_snapshot

    monkeypatch.setattr(review_service_module, "_recheck_project_terminal", _stub_recheck)

    async def _capture_dispatch(**kwargs):
        if dispatch_calls is not None:
            dispatch_calls.append(kwargs)
        return None

    import app.core.tasks as tasks_module
    monkeypatch.setattr(tasks_module, "dispatch_background_job", _capture_dispatch)

    async def _noop_release(_pid):
        return None

    import app.core.ai as ai_module
    monkeypatch.setattr(ai_module, "release_project_binding", _noop_release)

    async def _noop_on_completed(_db, _project):
        return None

    from app.billing.services import billing_guard as billing_guard_module
    monkeypatch.setattr(
        billing_guard_module.BillingGuard,
        "on_completed",
        staticmethod(_noop_on_completed),
    )

    async def _noop_pre_check(_db, _project, _user_id):
        return None

    monkeypatch.setattr(
        billing_guard_module.BillingGuard,
        "pre_check",
        staticmethod(_noop_pre_check),
    )

    async def _empty_retry_state(_db, _project):
        return {
            "retryable": False,
            "failed_node": None,
            "target_id": None,
            "failed_stage": None,
            "retry_action_label": "",
            "preserved_outputs": [],
            "reset_scope": [],
            "last_attempt": 0,
            "retry_running": False,
            "last_error": None,
        }

    import app.services.flow_retry_service as flow_retry_module
    monkeypatch.setattr(flow_retry_module, "build_review_retry_state", _empty_retry_state)


@pytest.fixture(autouse=True)
def _isolate_progress_cache():
    """每个测试前后清空 _progress_cache, 避免互相干扰。"""
    review_service_module._progress_cache.clear()
    yield
    review_service_module._progress_cache.clear()


async def _call_get_status(session: _FakeServiceSession, project: SimpleNamespace):
    service = review_service_module.ReviewService(session)  # type: ignore[arg-type]
    user = _make_user(owner_id=project.owner_id)
    return await service.get_review_status(project.id, user)  # type: ignore[arg-type]


# ============================================================
# (a) 基础: total_requirements=0 -> pending
# ============================================================


async def test_a_total_zero_returns_pending(monkeypatch):
    project = _make_project(project_id=1001)
    session = _FakeServiceSession(
        _make_count_responses(project=project, total=0, extracted=0, reviewed=0)
    )
    _install_status_stubs(monkeypatch)

    status = await _call_get_status(session, project)

    assert status.stage == REVIEW_STAGE_PENDING
    assert status.message == "等待启动审查"
    assert status.status == "processing"
    assert status.overall_progress == 0
    assert status.total_items == 0


# ============================================================
# (b) 提取中
# ============================================================


async def test_b_extracting_partial(monkeypatch):
    project = _make_project(project_id=1002)
    session = _FakeServiceSession(
        _make_count_responses(project=project, total=5, extracted=2, reviewed=0)
    )
    _install_status_stubs(monkeypatch)

    status = await _call_get_status(session, project)

    assert status.stage == REVIEW_STAGE_EXTRACTING
    assert "2/5" in status.message
    assert status.status == "processing"
    assert status.extraction_total == 5
    assert status.extraction_done == 2


# ============================================================
# (c) 装配中
# ============================================================


async def test_c_assembling_when_extracted_full_reviewed_zero(monkeypatch):
    project = _make_project(project_id=1003)
    session = _FakeServiceSession(
        _make_count_responses(project=project, total=5, extracted=5, reviewed=0)
    )
    _install_status_stubs(monkeypatch)

    status = await _call_get_status(session, project)

    assert status.stage == REVIEW_STAGE_ASSEMBLING
    assert status.message == "响应装配校验中"
    assert status.status == "processing"
    assert status.steps[1].status == "running"
    assert status.steps[2].status == "waiting"


# ============================================================
# (d) 审查中
# ============================================================


async def test_d_reviewing_partial(monkeypatch):
    project = _make_project(project_id=1004)
    session = _FakeServiceSession(
        _make_count_responses(project=project, total=5, extracted=5, reviewed=3)
    )
    _install_status_stubs(monkeypatch)

    status = await _call_get_status(session, project)

    assert status.stage == REVIEW_STAGE_REVIEWING
    assert "3/5" in status.message
    assert status.review_done == 3


# ============================================================
# (e) 结果已满但仍 REVIEWING + task live -> 保持 reviewing 
# ============================================================


async def test_e_reviewing_while_task_live_and_results_full(monkeypatch):
    """task 仍 live 时结果已满 -> 保持 REVIEWING，第3步 running、第4步 waiting。"""
    project = _make_project(project_id=1005)
    session = _FakeServiceSession(
        _make_count_responses(project=project, total=5, extracted=5, reviewed=5)
    )
    _install_status_stubs(monkeypatch, liveness=_make_liveness("live"))

    status = await _call_get_status(session, project)

    assert status.stage == REVIEW_STAGE_REVIEWING
    assert status.message == "AI审查收尾中"
    assert status.result_ready is True
    assert status.report_ready is True
    assert status.overall_status == "running"
    assert project.status == ProjectStatus.REVIEWING, "task 仍活时不应自动收敛"
    assert len(status.steps) == 4
    assert status.steps[2].status == "running"
    assert status.steps[2].label == "AI对比审查"
    # 主批次刚满、尚无 finalizing：停在 90，与重试起点衔接
    assert status.steps[2].progress == 90
    assert status.steps[3].status == "waiting"
    assert status.steps[3].label == "结果就绪"


# ============================================================
# (f) Project.COMPLETED
# ============================================================


async def test_f_completed_status(monkeypatch):
    project = _make_project(
        project_id=1006, status=ProjectStatus.COMPLETED
    )
    session = _FakeServiceSession(
        _make_count_responses(project=project, total=5, extracted=5, reviewed=5)
    )
    reset_calls: list[str] = []
    _install_status_stubs(monkeypatch, reset_calls=reset_calls)

    status = await _call_get_status(session, project)

    assert status.stage == REVIEW_STAGE_COMPLETED
    assert status.message == "审查完成"
    assert status.progress == 1.0
    assert status.status == "completed"
    assert status.overall_progress == 100
    assert "review_project_1006" in reset_calls, "COMPLETED 分支应 reset_recovery_count"


# ============================================================
# (g) Project.FAILED
# ============================================================


async def test_g_failed_status(monkeypatch):
    project = _make_project(
        project_id=1007,
        status=ProjectStatus.FAILED,
        error_message="历史错误",
    )
    session = _FakeServiceSession(
        _make_count_responses(project=project, total=5, extracted=2, reviewed=0)
    )
    _install_status_stubs(monkeypatch)

    status = await _call_get_status(session, project)

    assert status.stage == REVIEW_STAGE_FAILED
    assert status.status == "failed"
    assert status.message == "审查失败"


# ============================================================
# (h) 状态收敛兜底: 结果满 + task 不活 -> 自动补 COMPLETED
# ============================================================


async def test_h_converge_completed_when_results_ready_but_task_dead(monkeypatch):
    """结果就绪但 Project.status 未收敛, 应自动写 COMPLETED 而非重投."""
    project = _make_project(project_id=1008)
    session = _FakeServiceSession(
        _make_count_responses(project=project, total=3, extracted=3, reviewed=3)
    )
    dispatch_calls: list = []
    _install_status_stubs(
        monkeypatch,
        liveness=_make_liveness("missing"),
        dispatch_calls=dispatch_calls,
    )

    status = await _call_get_status(session, project)

    assert project.status == ProjectStatus.COMPLETED
    assert status.status == "completed"
    assert status.stage == REVIEW_STAGE_COMPLETED
    assert dispatch_calls == [], "已就绪兜底分支不得触发任务重投"
    assert session.commits >= 1


# ============================================================
# (i) state=stale + reviewed<total -> Project 转 FAILED
# ============================================================


async def test_i_stale_heartbeat_transitions_to_failed(monkeypatch):
    project = _make_project(project_id=1009)
    session = _FakeServiceSession(
        _make_count_responses(project=project, total=5, extracted=2, reviewed=0)
    )
    _install_status_stubs(monkeypatch, liveness=_make_liveness("stale"))

    status = await _call_get_status(session, project)

    assert project.status == ProjectStatus.FAILED
    assert status.status == "failed"
    assert "心跳超时" in (project.error_message or "")


async def test_i_stale_heartbeat_stays_reviewing_when_arq_inflight(monkeypatch):
    """: 心跳 stale 但 ARQ 仍在途时，不得写 FAILED（否则前端重试叠跑）。"""
    project = _make_project(project_id=1100)
    session = _FakeServiceSession(
        _make_count_responses(project=project, total=5, extracted=2, reviewed=0)
    )
    _install_status_stubs(
        monkeypatch,
        liveness=_make_liveness("stale"),
        arq_inflight=True,
    )

    status = await _call_get_status(session, project)

    assert project.status == ProjectStatus.REVIEWING
    assert status.status != "failed"
    assert "心跳超时" not in (project.error_message or "")


async def test_i_worker_missing_stale_uses_disconnect_copy_not_heartbeat(monkeypatch):
    """: reason=worker_missing 不得误报心跳超时。"""
    project = _make_project(project_id=1028)
    session = _FakeServiceSession(
        _make_count_responses(project=project, total=5, extracted=2, reviewed=0)
    )
    _install_status_stubs(
        monkeypatch,
        liveness=_make_liveness(
            "stale",
            reason="worker_missing:review_project_job",
            age_seconds=35.1,
        ),
    )

    status = await _call_get_status(session, project)

    assert project.status == ProjectStatus.FAILED
    assert status.status == "failed"
    assert "Worker 失联" in (project.error_message or "")
    assert "心跳超时" not in (project.error_message or "")


# ============================================================
# (j) state=missing + reviewed<total -> Project 转 FAILED
# ============================================================


async def test_j_missing_task_transitions_to_failed(monkeypatch):
    project = _make_project(project_id=1010)
    session = _FakeServiceSession(
        _make_count_responses(project=project, total=5, extracted=2, reviewed=0)
    )
    _install_status_stubs(monkeypatch, liveness=_make_liveness("missing"))

    status = await _call_get_status(session, project)

    assert project.status == ProjectStatus.FAILED
    assert status.status == "failed"
    assert "异常终止" in (project.error_message or "")


# ============================================================
# (k) state=terminal + recovery<MAX -> 自动恢复 dispatch
# ============================================================


async def test_k_terminal_state_triggers_auto_recover(monkeypatch):
    """state=terminal (worker 已退出但 project.status 仍 REVIEWING) -> 触发自动恢复."""
    project = _make_project(project_id=1011)
    session = _FakeServiceSession(
        _make_count_responses(project=project, total=5, extracted=2, reviewed=0)
    )
    dispatch_calls: list = []
    increment_calls: list = []
    cancel_calls: list = []
    _install_status_stubs(
        monkeypatch,
        liveness=_make_liveness("terminal"),
        recovery_count=0,
        terminal_snapshot=None,
        dispatch_calls=dispatch_calls,
        increment_calls=increment_calls,
        cancel_calls=cancel_calls,
    )

    status = await _call_get_status(session, project)

    assert len(dispatch_calls) == 1, "应 dispatch 一次自动恢复任务"
    assert "review_project_1011" in cancel_calls, "应先 cancel 旧任务句柄"
    assert "review_project_1011" in increment_calls, "应递增 recovery_count"
    assert status.is_recovering is True
    assert "自动恢复" in status.message


def _install_recovery_billing_precheck(
    monkeypatch: pytest.MonkeyPatch,
    *,
    raise_insufficient: bool = False,
) -> list:
    """桩接 BillingGuard.pre_check; 丢失恢复路径还需把 inflight 守卫钉成未命中。"""
    calls: list = []
    from app.billing.services import billing_guard as billing_guard_module
    from app.billing.services.wallet_service import InsufficientBalanceError

    async def _pre_check(_db, project, _user_id):
        calls.append(project.id)
        if raise_insufficient:
            raise InsufficientBalanceError(available=0, required=400)

    monkeypatch.setattr(
        billing_guard_module.BillingGuard,
        "pre_check",
        staticmethod(_pre_check),
    )

    async def _not_inflight(*_a, **_k):
        return False

    import app.core.tasks.lifecycle.liveness_evidence as liveness_evidence_module
    monkeypatch.setattr(
        liveness_evidence_module,
        "is_arq_job_possibly_inflight",
        _not_inflight,
    )
    return calls


async def test_k2_lost_recovery_skips_dispatch_when_balance_insufficient(monkeypatch):
    """: 丢失恢复遇余额不足不得派发、不得消耗恢复配额。"""
    project = _make_project(project_id=1021)
    session = _FakeServiceSession(
        _make_count_responses(project=project, total=5, extracted=2, reviewed=0)
    )
    dispatch_calls: list = []
    increment_calls: list = []
    _install_status_stubs(
        monkeypatch,
        liveness=_make_liveness("terminal"),
        recovery_count=0,
        terminal_snapshot=None,
        dispatch_calls=dispatch_calls,
        increment_calls=increment_calls,
    )
    precheck_calls = _install_recovery_billing_precheck(
        monkeypatch, raise_insufficient=True,
    )

    status = await _call_get_status(session, project)

    assert precheck_calls == [1021]
    assert dispatch_calls == [], "余额不足不得自动重投"
    assert increment_calls == [], "余额不足不得消耗恢复配额"
    assert project.status == ProjectStatus.REVIEWING
    assert status.is_recovering is True
    assert "余额不足" in (status.message or "")


async def test_k3_lost_recovery_dispatches_after_billing_precheck(monkeypatch):
    """: 丢失恢复预检通过后仍按原路径重投。"""
    project = _make_project(project_id=1022)
    session = _FakeServiceSession(
        _make_count_responses(project=project, total=5, extracted=2, reviewed=0)
    )
    dispatch_calls: list = []
    increment_calls: list = []
    _install_status_stubs(
        monkeypatch,
        liveness=_make_liveness("terminal"),
        recovery_count=0,
        terminal_snapshot=None,
        dispatch_calls=dispatch_calls,
        increment_calls=increment_calls,
    )
    precheck_calls = _install_recovery_billing_precheck(monkeypatch)

    status = await _call_get_status(session, project)

    assert precheck_calls == [1022]
    assert len(dispatch_calls) == 1
    assert "review_project_1022" in increment_calls
    assert status.is_recovering is True
    assert "自动恢复" in status.message


# ============================================================
# (l) state=terminal + recovery>=MAX -> 转 FAILED
# ============================================================


async def test_l_recovery_exhausted_transitions_to_failed(monkeypatch):
    from app.core.tasks import MAX_RECOVERY

    project = _make_project(project_id=1012)
    session = _FakeServiceSession(
        _make_count_responses(project=project, total=5, extracted=2, reviewed=0)
    )
    dispatch_calls: list = []
    _install_status_stubs(
        monkeypatch,
        liveness=_make_liveness("terminal"),
        recovery_count=MAX_RECOVERY,
        terminal_snapshot=None,
        dispatch_calls=dispatch_calls,
    )

    status = await _call_get_status(session, project)

    assert project.status == ProjectStatus.FAILED
    assert status.status == "failed"
    assert dispatch_calls == [], "达到上限不得继续 dispatch"
    assert str(MAX_RECOVERY) in (project.error_message or "")


# ============================================================
# (m) state=enqueue_grace -> 不触发任何恢复
# ============================================================


async def test_m_enqueue_grace_skips_recovery(monkeypatch):
    project = _make_project(project_id=1013)
    session = _FakeServiceSession(
        _make_count_responses(project=project, total=5, extracted=0, reviewed=0)
    )
    dispatch_calls: list = []
    increment_calls: list = []
    _install_status_stubs(
        monkeypatch,
        liveness=_make_liveness("enqueue_grace"),
        dispatch_calls=dispatch_calls,
        increment_calls=increment_calls,
    )

    status = await _call_get_status(session, project)

    assert project.status == ProjectStatus.REVIEWING, "宽限期不得改 Project 状态"
    assert dispatch_calls == [], "宽限期不得 dispatch"
    assert increment_calls == [], "宽限期不得 increment recovery"
    assert status.status == "processing"


# ============================================================
# (n) 进度停滞 + 心跳 fresh -> 不重启
# ============================================================


async def test_n_stalled_with_fresh_heartbeat_skips_restart(monkeypatch):
    """task 仍活但 DB 计数未更新; 若心跳 fresh, 应跳过重启."""
    from app.core.config import settings

    project = _make_project(project_id=1014)
    session = _FakeServiceSession(
        _make_count_responses(project=project, total=5, extracted=2, reviewed=0)
    )

    # 预填 _progress_cache 让停滞条件成立 (cached.processed >= now.processed,
    # 且 updated_at 已超过 REVIEW_STALL_TIMEOUT)
    stale_ts = time.time() - settings.REVIEW_STALL_TIMEOUT - 30
    review_service_module._progress_cache[project.id] = {
        "processed": 2.0,
        "updated_at": stale_ts,
    }

    fresh_heartbeat = {
        "stage": "extracting",
        "last_heartbeat": time.time() - 5,
    }
    dispatch_calls: list = []
    _install_status_stubs(
        monkeypatch,
        liveness=_make_liveness("live", heartbeat=fresh_heartbeat),
        heartbeat=fresh_heartbeat,
        dispatch_calls=dispatch_calls,
    )

    await _call_get_status(session, project)

    assert dispatch_calls == [], "心跳新鲜时不得重启任务"


# ============================================================
# (o) 进度停滞 + 心跳 stale -> 重启
# ============================================================


async def test_o_stalled_with_stale_heartbeat_restarts(monkeypatch):
    from app.core.config import settings
    from app.core.tasks import HEARTBEAT_TIMEOUT

    project = _make_project(project_id=1015)
    session = _FakeServiceSession(
        _make_count_responses(project=project, total=5, extracted=2, reviewed=0)
    )

    stale_progress_ts = time.time() - settings.REVIEW_STALL_TIMEOUT - 30
    review_service_module._progress_cache[project.id] = {
        "processed": 2.0,
        "updated_at": stale_progress_ts,
    }

    stale_heartbeat = {
        "stage": "extracting",
        "last_heartbeat": time.time() - HEARTBEAT_TIMEOUT - 30,
    }
    dispatch_calls: list = []
    increment_calls: list = []
    _install_status_stubs(
        monkeypatch,
        liveness=_make_liveness("live", heartbeat=stale_heartbeat),
        heartbeat=stale_heartbeat,
        terminal_snapshot=None,
        dispatch_calls=dispatch_calls,
        increment_calls=increment_calls,
    )

    status = await _call_get_status(session, project)

    assert len(dispatch_calls) == 1, "心跳过期时停滞分支应重启任务"
    assert status.is_recovering is True


async def test_o2_stall_restart_skips_dispatch_when_balance_insufficient(monkeypatch):
    """: 停滞重启遇余额不足不得派发、不得消耗恢复配额。"""
    from app.core.config import settings
    from app.core.tasks import HEARTBEAT_TIMEOUT

    project = _make_project(project_id=1023)
    session = _FakeServiceSession(
        _make_count_responses(project=project, total=5, extracted=2, reviewed=0)
    )
    stale_progress_ts = time.time() - settings.REVIEW_STALL_TIMEOUT - 30
    review_service_module._progress_cache[project.id] = {
        "processed": 2.0,
        "updated_at": stale_progress_ts,
    }
    stale_heartbeat = {
        "stage": "extracting",
        "last_heartbeat": time.time() - HEARTBEAT_TIMEOUT - 30,
    }
    dispatch_calls: list = []
    increment_calls: list = []
    _install_status_stubs(
        monkeypatch,
        liveness=_make_liveness("live", heartbeat=stale_heartbeat),
        heartbeat=stale_heartbeat,
        terminal_snapshot=None,
        dispatch_calls=dispatch_calls,
        increment_calls=increment_calls,
    )
    precheck_calls = _install_recovery_billing_precheck(
        monkeypatch, raise_insufficient=True,
    )

    status = await _call_get_status(session, project)

    assert precheck_calls == [1023]
    assert dispatch_calls == [], "余额不足不得停滞重启"
    assert increment_calls == [], "余额不足不得消耗恢复配额"
    assert project.status == ProjectStatus.REVIEWING
    assert status.is_recovering is True
    assert "余额不足" in (status.message or "")


# ============================================================
# (p) report_ready + COMPLETED -> 4 steps 全 completed + progress=100
# ============================================================


async def test_p_completed_steps_all_done(monkeypatch):
    project = _make_project(
        project_id=1016, status=ProjectStatus.COMPLETED
    )
    session = _FakeServiceSession(
        _make_count_responses(project=project, total=5, extracted=5, reviewed=5)
    )
    _install_status_stubs(monkeypatch)

    status = await _call_get_status(session, project)

    assert status.report_ready is True
    assert status.result_ready is True
    assert len(status.steps) == 4
    for step in status.steps:
        assert step.status == "completed"
        assert step.progress == 100
    assert status.overall_progress == 100
    assert status.overall_status == "completed"


# ============================================================
# (q) reviewed==total + phase=self_healing -> reviewing + 重试 detail
# ============================================================


async def test_q_self_healing_keeps_review_step_running(monkeypatch):
    """: 失败项重试期间第3步 running，从主批次终点 90% 接续，无回跳。"""
    project = _make_project(project_id=1017)
    session = _FakeServiceSession(
        _make_count_responses(project=project, total=5, extracted=5, reviewed=5)
    )
    healing_heartbeat = {
        "stage": "步骤4-重试",
        "progress": "0/4",
        "phase": "self_healing",
        "last_heartbeat": time.time(),
    }
    _install_status_stubs(
        monkeypatch,
        liveness=_make_liveness("live", heartbeat=healing_heartbeat),
        heartbeat=healing_heartbeat,
    )

    status = await _call_get_status(session, project)

    assert status.stage == REVIEW_STAGE_REVIEWING
    assert status.message == "失败项重试中"
    assert status.overall_status == "running"
    assert status.steps[2].status == "running"
    assert status.steps[2].progress == 90
    assert status.steps[2].progress < 100
    assert status.steps[2].detail is not None
    assert "0/4" in status.steps[2].detail
    assert "失败项重试" in status.steps[2].detail
    assert status.steps[3].status == "waiting"
    assert project.status == ProjectStatus.REVIEWING


async def test_q2_self_healing_progress_advances_with_retry(monkeypatch):
    """: 重试 2/4 时第3步进度推进到 94，仍未满格。"""
    project = _make_project(project_id=1018)
    session = _FakeServiceSession(
        _make_count_responses(project=project, total=5, extracted=5, reviewed=5)
    )
    healing_heartbeat = {
        "stage": "步骤4-重试",
        "progress": "2/4",
        "phase": "self_healing",
        "last_heartbeat": time.time(),
    }
    _install_status_stubs(
        monkeypatch,
        liveness=_make_liveness("live", heartbeat=healing_heartbeat),
        heartbeat=healing_heartbeat,
    )

    status = await _call_get_status(session, project)

    assert status.steps[2].status == "running"
    assert status.steps[2].progress == 94
    assert "2/4" in (status.steps[2].detail or "")
    assert status.steps[3].status == "waiting"


async def test_q3_main_batch_caps_at_90_before_retry_band(monkeypatch):
    """: 主批次未完成时进度落在 0-90；刚满无 phase 时保持 90；finalizing 才到 99。"""
    project = _make_project(project_id=1019)

    session_partial = _FakeServiceSession(
        _make_count_responses(project=project, total=10, extracted=10, reviewed=9)
    )
    _install_status_stubs(monkeypatch, liveness=_make_liveness("live"))
    status_partial = await _call_get_status(session_partial, project)
    assert status_partial.steps[2].status == "running"
    assert status_partial.steps[2].progress == 81  # 9/10 * 90
    assert status_partial.steps[2].progress <= 90

    session_full = _FakeServiceSession(
        _make_count_responses(project=project, total=10, extracted=10, reviewed=10)
    )
    _install_status_stubs(monkeypatch, liveness=_make_liveness("live"))
    status_full = await _call_get_status(session_full, project)
    assert status_full.steps[2].progress == 90

    session_fin = _FakeServiceSession(
        _make_count_responses(project=project, total=10, extracted=10, reviewed=10)
    )
    fin_heartbeat = {
        "stage": "步骤4-收尾",
        "progress": "10/10",
        "phase": "finalizing",
        "last_heartbeat": time.time(),
    }
    _install_status_stubs(
        monkeypatch,
        liveness=_make_liveness("live", heartbeat=fin_heartbeat),
        heartbeat=fin_heartbeat,
    )
    status_fin = await _call_get_status(session_fin, project)
    assert status_fin.steps[2].progress == 99
    assert status_fin.message == "AI审查收尾中"


# ============================================================
# 运行中步骤进度取 max(落库比例, 心跳 stage_progress_pct)
# ============================================================


async def test_extracting_zero_persisted_uses_inflight_heartbeat_pct(monkeypatch):
    """落库仍为 0 时，提取步条宽必须跟心跳在途百分比，明细为 0/N。"""
    project = _make_project(project_id=1031)
    session = _FakeServiceSession(
        _make_count_responses(project=project, total=10, extracted=0, reviewed=0)
    )
    heartbeat = {
        "stage": "步骤2-提取",
        "progress": "0/10",
        "stage_progress_pct": 20,
        "current": 0,
        "total": 10,
        "inflight": 8,
        "last_heartbeat": time.time(),
    }
    _install_status_stubs(
        monkeypatch,
        liveness=_make_liveness("live", heartbeat=heartbeat),
        heartbeat=heartbeat,
    )

    status = await _call_get_status(session, project)

    assert status.stage == REVIEW_STAGE_EXTRACTING
    assert status.steps[0].status == "running"
    assert status.steps[0].progress == 20
    assert status.steps[0].detail == "0/10 · 在途 8"
    assert status.overall_progress > 0


async def test_reviewing_zero_persisted_uses_step4_inflight_heartbeat(monkeypatch):
    """: 提取已满、审查落库仍为 0，心跳已到步骤4时当前步必须是审查。"""
    project = _make_project(project_id=1051)
    session = _FakeServiceSession(
        _make_count_responses(project=project, total=10, extracted=10, reviewed=0)
    )
    heartbeat = {
        "stage": "步骤4-审查",
        "progress": "0/10",
        "stage_progress_pct": 20,
        "current": 0,
        "total": 10,
        "inflight": 8,
        "last_heartbeat": time.time(),
    }
    _install_status_stubs(
        monkeypatch,
        liveness=_make_liveness("live", heartbeat=heartbeat),
        heartbeat=heartbeat,
    )

    status = await _call_get_status(session, project)

    assert status.stage == REVIEW_STAGE_REVIEWING
    assert status.steps[1].status == "completed"
    assert status.steps[2].status == "running"
    assert status.steps[2].progress == 20
    assert status.steps[2].detail == "0/10 · 在途 8"
    assert "AI审查中" in status.message


async def test_reviewing_stale_zero_heartbeat_keeps_persisted_count(monkeypatch):
    """: 落库计数高于心跳开工分数时，文案取落库。"""
    project = _make_project(project_id=1083)
    session = _FakeServiceSession(
        _make_count_responses(project=project, total=43, extracted=43, reviewed=27)
    )
    heartbeat = {
        "stage": "步骤4-审查",
        "progress": "0/43",
        "stage_progress_pct": 20,
        "current": 0,
        "total": 43,
        "inflight": 10,
        "last_heartbeat": time.time(),
    }
    _install_status_stubs(
        monkeypatch,
        liveness=_make_liveness("live", heartbeat=heartbeat),
        heartbeat=heartbeat,
    )

    status = await _call_get_status(session, project)

    assert status.stage == REVIEW_STAGE_REVIEWING
    assert status.steps[2].status == "running"
    assert status.steps[2].detail == "27/43"


async def test_assembling_heartbeat_keeps_review_waiting(monkeypatch):
    """同形态第二例：计数相同但心跳仍属步骤3时，不得把审查步点亮。"""
    project = _make_project(project_id=1052)
    session = _FakeServiceSession(
        _make_count_responses(project=project, total=10, extracted=10, reviewed=0)
    )
    heartbeat = {
        "stage": "步骤3-匹配",
        "progress": "2/10",
        "stage_progress_pct": 20,
        "substage": "assemble_pairs",
        "last_heartbeat": time.time(),
    }
    _install_status_stubs(
        monkeypatch,
        liveness=_make_liveness("live", heartbeat=heartbeat),
        heartbeat=heartbeat,
    )

    status = await _call_get_status(session, project)

    assert status.stage == REVIEW_STAGE_ASSEMBLING
    assert status.steps[1].status == "running"
    assert status.steps[2].status == "waiting"
    assert status.steps[1].progress == 20


async def test_reviewing_heartbeat_pct_lifts_bar_ahead_of_persisted(monkeypatch):
    """同形态第二例：审查步落库落后于心跳百分数时，条宽跟心跳。"""
    project = _make_project(project_id=1032)
    session = _FakeServiceSession(
        _make_count_responses(project=project, total=10, extracted=10, reviewed=1)
    )
    heartbeat = {
        "stage": "步骤4-审查",
        "progress": "1/10",
        "stage_progress_pct": 35,
        "last_heartbeat": time.time(),
    }
    _install_status_stubs(
        monkeypatch,
        liveness=_make_liveness("live", heartbeat=heartbeat),
        heartbeat=heartbeat,
    )

    status = await _call_get_status(session, project)

    assert status.stage == REVIEW_STAGE_REVIEWING
    assert status.steps[2].status == "running"
    assert status.steps[2].progress == 35
    assert status.steps[2].detail == "1/10"


async def test_reviewing_does_not_inherit_assemble_leftover_pct(monkeypatch):
    """: 审查文案 3/10 时不得吃装配残留 stage_progress_pct=100。"""
    project = _make_project(project_id=1033)
    session = _FakeServiceSession(
        _make_count_responses(project=project, total=10, extracted=10, reviewed=3)
    )
    heartbeat = {
        "stage": "步骤4-审查",
        "progress": "3/10",
        "stage_progress_pct": 100,
        "substage": "assemble_pairs",
        "current": 10,
        "total": 10,
        "last_heartbeat": time.time(),
    }
    _install_status_stubs(
        monkeypatch,
        liveness=_make_liveness("live", heartbeat=heartbeat),
        heartbeat=heartbeat,
    )

    status = await _call_get_status(session, project)

    assert status.stage == REVIEW_STAGE_REVIEWING
    assert status.steps[2].status == "running"
    assert status.steps[2].detail == "3/10"
    assert status.steps[2].progress == 27  # 3/10 * 90
    assert status.steps[2].progress < 90


async def test_reviewing_ignores_stale_finalizing_phase_on_main_batch(monkeypatch):
    """同形态：上一轮 finalizing 残留不得把主批次 3/10 打到 99。"""
    project = _make_project(project_id=1034)
    session = _FakeServiceSession(
        _make_count_responses(project=project, total=10, extracted=10, reviewed=3)
    )
    heartbeat = {
        "stage": "步骤4-审查",
        "progress": "3/10",
        "phase": "finalizing",
        "last_heartbeat": time.time(),
    }
    _install_status_stubs(
        monkeypatch,
        liveness=_make_liveness("live", heartbeat=heartbeat),
        heartbeat=heartbeat,
    )

    status = await _call_get_status(session, project)

    assert status.steps[2].status == "running"
    assert status.steps[2].progress == 27
    assert status.steps[2].detail == "3/10"


# ============================================================
# 装配步条宽必须带阶段归属，禁止吞上一步满格
# ============================================================


async def test_assembling_ignores_extract_fraction_heartbeat(monkeypatch):
    """提取已满、心跳仍是步骤2 N/N 时，装配步不得打满且保持 running。"""
    project = _make_project(project_id=1111)
    session = _FakeServiceSession(
        _make_count_responses(project=project, total=42, extracted=42, reviewed=0)
    )
    heartbeat = {
        "stage": "步骤2-提取",
        "progress": "42/42",
        "stage_progress_pct": 100,
        "current": 42,
        "total": 42,
        "last_heartbeat": time.time(),
    }
    _install_status_stubs(
        monkeypatch,
        liveness=_make_liveness("live", heartbeat=heartbeat),
        heartbeat=heartbeat,
    )

    status = await _call_get_status(session, project)

    assert status.stage == REVIEW_STAGE_ASSEMBLING
    assert status.steps[1].label == "响应装配校验"
    assert status.steps[1].status == "running"
    assert status.steps[1].progress == 0
    assert status.steps[1].detail != "42/42"


async def test_assembling_follows_matching_heartbeat_fraction(monkeypatch):
    """同形态第二例：装配心跳 3/10 时条宽跟本步分数，完成色仍不提前。"""
    project = _make_project(project_id=1112)
    session = _FakeServiceSession(
        _make_count_responses(project=project, total=10, extracted=10, reviewed=0)
    )
    heartbeat = {
        "stage": "步骤3-匹配",
        "progress": "3/10",
        "stage_progress_pct": 30,
        "substage": "assemble_pairs",
        "current": 3,
        "total": 10,
        "last_heartbeat": time.time(),
    }
    _install_status_stubs(
        monkeypatch,
        liveness=_make_liveness("live", heartbeat=heartbeat),
        heartbeat=heartbeat,
    )

    status = await _call_get_status(session, project)

    assert status.stage == REVIEW_STAGE_ASSEMBLING
    assert status.steps[1].status == "running"
    assert status.steps[1].progress == 30
    assert status.steps[1].detail == "3/10"
    assert status.steps[2].status == "waiting"
