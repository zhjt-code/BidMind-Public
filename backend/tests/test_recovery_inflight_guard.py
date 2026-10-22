""" 恢复守卫防御测试基线。

覆盖场景：
 A. is_arq_job_possibly_inflight 单元（场景 1-7）
 B. review_service._auto_recover_lost_task 守卫接线集成（场景 8-9）
 C. collusion_status_service 守卫接线静态验证（场景 10）

场景 10 说明：
 compute_collusion_status 依赖多层嵌套的 SQL 聚合、权限校验、心跳超时计算等逻辑，
 构造完整可运行的 fake session 需要 12+ 次 execute 调用且依赖
 project / Document / CollusionExtraction / CollusionResult 等多个 ORM model 对象。
 改用 inspect.getsource 静态断言代价更低，且能直接锁定"守卫调用与 else 分支并存"
 这一核心结构不变性，等同于防止任何重构意外抹掉守卫或 else 分支。

patch 策略：
 is_arq_job_possibly_inflight 函数体内有两处 local import：
 from app.core.arq_runtime import arq_job_present_async
 from app.core.redis import get_redis_pool
 local import 在运行时从 sys.modules 查属性，因此 patch 源模块属性即可拦截。
 目标：
 - app.core.redis.get_redis_pool
 - app.core.arq_runtime.arq_job_present_async
 review 接线同理：守卫在函数体内 import，patch 源模块
 app.core.tasks.lifecycle.liveness_evidence.is_arq_job_possibly_inflight
"""

from __future__ import annotations

import inspect
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock, Mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import app.core.arq_runtime as arq_runtime_module
import app.core.redis as redis_module
import app.core.tasks.lifecycle as lifecycle_module
import app.core.tasks.lifecycle.liveness_evidence as liveness_evidence_module
from app.core.tasks import TaskLiveness
from app.core.tasks.lifecycle.liveness_evidence import is_arq_job_possibly_inflight
from app.models.project import ProjectStatus
from app.models.user import UserRole
from app.services import review_service as review_service_module

# ============================================================
# 通用桩
# ============================================================


class _FakeExecResult:
    """最小 DB 执行结果桩，支持 scalar / scalar_one_or_none / scalars().all()。"""

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
    """按调用顺序返回预置 execute 响应的最小 DB 桩。"""

    def __init__(self, responses: list[_FakeExecResult]) -> None:
        self._responses = responses
        self._idx = 0
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, _stmt):
        if self._idx >= len(self._responses):
            raise AssertionError(
                f"execute() 调用次数 {self._idx + 1} 超过预置响应队列长度 {len(self._responses)}"
            )
        value = self._responses[self._idx]
        self._idx += 1
        return value

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


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


def _make_terminal_liveness(
    task_name: str = "review_project_test",
    heartbeat: Optional[dict] = None,
) -> TaskLiveness:
    """构造 state=terminal 的 TaskLiveness，is_live=False。"""
    return TaskLiveness(
        task_name=task_name,
        state="terminal",
        active=False,
        heartbeat=heartbeat,
        stage=str((heartbeat or {}).get("stage") or "").lower(),
        age_seconds=0.0,
        reason="test_terminal",
    )


def _make_review_count_responses(
    *,
    project: SimpleNamespace,
    total: int = 5,
    extracted: int = 2,
    reviewed: int = 0,
) -> list[_FakeExecResult]:
    """get_review_status 的固定 5 次 execute 顺序：
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


def _install_review_recovery_stubs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    recovery_count: int = 0,
    heartbeat: Optional[dict] = None,
    terminal_snapshot=None,
    liveness: Optional[TaskLiveness] = None,
    dispatch_calls: Optional[list] = None,
    cancel_calls: Optional[list] = None,
    increment_calls: Optional[list] = None,
    reset_calls: Optional[list] = None,
    guard_return: bool = False,
) -> None:
    """安装 get_review_status 在 terminal+recovery 场景下所需的全部外部依赖桩。"""
    if liveness is None:
        liveness = _make_terminal_liveness(heartbeat=heartbeat)

    monkeypatch.setattr(
        review_service_module,
        "inspect_task_liveness",
        lambda *_a, **_k: liveness,
    )
    monkeypatch.setattr(
        review_service_module, "get_recovery_count", lambda _n: recovery_count
    )
    monkeypatch.setattr(
        review_service_module, "get_task_heartbeat", lambda _n: heartbeat
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

    monkeypatch.setattr(
        review_service_module, "_recheck_project_terminal", _stub_recheck
    )

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

    monkeypatch.setattr(
        flow_retry_module, "build_review_retry_state", _empty_retry_state
    )

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

    async def _noop_on_failed(_db, _project):
        return None

    monkeypatch.setattr(
        billing_guard_module.BillingGuard,
        "on_failed",
        staticmethod(_noop_on_failed),
    )

    # 守卫本体：patch 源模块属性，令函数体内的 local import 取得桩
    async def _mock_guard(hb, *, task_name, arq_function=None):
        return guard_return

    monkeypatch.setattr(
        liveness_evidence_module, "is_arq_job_possibly_inflight", _mock_guard
    )


@pytest.fixture(autouse=True)
def _isolate_progress_cache():
    """每个测试前后清空 _progress_cache，避免跨测试干扰。"""
    review_service_module._progress_cache.clear()
    yield
    review_service_module._progress_cache.clear()


async def _call_get_status(session: _FakeServiceSession, project: SimpleNamespace):
    service = review_service_module.ReviewService(session)  # type: ignore[arg-type]
    user = SimpleNamespace(id=project.owner_id, role=UserRole.ADMIN)
    return await service.get_review_status(project.id, user)  # type: ignore[arg-type]


# ============================================================
# A. is_arq_job_possibly_inflight 单元测试（场景 1-7）
# ============================================================


async def test_a1_none_heartbeat_without_pool_returns_false(monkeypatch):
    """场景1(更新)：heartbeat 为 None 且 Redis 池不可用 → 回退旧语义返回 False。

 心跳缺失分支是新增守卫, 探测不可用时必须允许恢复, 否则 Redis 故障会连
 asyncio 兜底恢复一起阻断。
 """
    monkeypatch.setattr(redis_module, "get_redis_pool", AsyncMock(return_value=None))
    result = await is_arq_job_possibly_inflight(None, task_name="review_project_1")
    assert result is False, "heartbeat 为 None 且无 Redis 池时应返回 False(允许恢复)"


async def test_a1b_none_heartbeat_with_queue_trace_blocks_recovery(monkeypatch):
    """场景1b：heartbeat 被清理但按 task_name 补探到队列痕迹且 worker 存活 → True。

 心跳键被 cleanup/驱逐不代表队列无 job, 此前守卫在该情形下直接放行重投。
 """
    pool_mock = object()
    captured_job_ids: list[str] = []

    async def _mock_present(pool, job_id):
        captured_job_ids.append(job_id)
        return True

    monkeypatch.setattr(redis_module, "get_redis_pool", AsyncMock(return_value=pool_mock))
    monkeypatch.setattr(arq_runtime_module, "arq_job_present_async", _mock_present)
    monkeypatch.setattr(arq_runtime_module, "has_alive_worker_for", Mock(return_value=True))

    result = await is_arq_job_possibly_inflight(
        None, task_name="review_project_1b", arq_function="review_project_job"
    )

    assert result is True, "心跳缺失但队列有痕迹且 worker 存活时应视为在途"
    assert captured_job_ids == ["review_project_1b"], "补探必须用 task_name 作为确定性 job_id"


async def test_a1c_none_heartbeat_probe_error_returns_false(monkeypatch):
    """场景1c：heartbeat 为 None 且补探抛异常 → 回退旧「允许恢复」语义。

 与场景6(有心跳时异常保守视为在途)方向相反: 新增守卫不可用时不得改变既有行为。
 """
    monkeypatch.setattr(
        redis_module,
        "get_redis_pool",
        AsyncMock(side_effect=RuntimeError("redis 连接超时")),
    )
    result = await is_arq_job_possibly_inflight(None, task_name="review_project_1c")
    assert result is False, "心跳缺失分支探测异常时应返回 False(允许恢复)"


async def test_a2_asyncio_fallback_returns_false_no_redis_call(monkeypatch):
    """场景2：queue_mode=asyncio_fallback，返回 False，且不应调用 get_redis_pool。

    本地 asyncio 兜底路径已由 cancel_task 真实取消，不存在远端残留。
    """
    mock_get_pool = Mock()
    monkeypatch.setattr(redis_module, "get_redis_pool", mock_get_pool)

    result = await is_arq_job_possibly_inflight(
        {"queue_mode": "asyncio_fallback"}, task_name="review_project_2"
    )

    assert result is False, "queue_mode 非 arq 时应返回 False"
    assert mock_get_pool.call_count == 0, (
        "queue_mode 非 arq 时不应触达 Redis，get_redis_pool 不应被调用"
    )


async def test_a3_arq_job_present_true_returns_true(monkeypatch):
    """场景3：queue_mode=arq + arq_job_id 存在 + arq_job_present_async=True → True。"""
    pool_mock = object()
    monkeypatch.setattr(redis_module, "get_redis_pool", AsyncMock(return_value=pool_mock))
    monkeypatch.setattr(
        arq_runtime_module, "arq_job_present_async", AsyncMock(return_value=True)
    )

    result = await is_arq_job_possibly_inflight(
        {"queue_mode": "arq", "arq_job_id": "job-abc123"}, task_name="review_project_3"
    )

    assert result is True, "arq_job_present_async 返回 True 时守卫应返回 True"


async def test_a4_arq_job_present_false_returns_false(monkeypatch):
    """场景4：queue_mode=arq + arq_job_id 存在 + arq_job_present_async=False → False。"""
    pool_mock = object()
    monkeypatch.setattr(redis_module, "get_redis_pool", AsyncMock(return_value=pool_mock))
    monkeypatch.setattr(
        arq_runtime_module, "arq_job_present_async", AsyncMock(return_value=False)
    )

    result = await is_arq_job_possibly_inflight(
        {"queue_mode": "arq", "arq_job_id": "job-abc456"}, task_name="review_project_4"
    )

    assert result is False, "arq_job_present_async 返回 False 时守卫应返回 False"


async def test_a5_missing_job_id_falls_back_to_task_name(monkeypatch):
    """场景5：queue_mode=arq 但 arq_job_id 缺失，应以 task_name 作为 job_id 兜底。

    断言实际传给 arq_job_present_async 的 job_id 参数等于 task_name。
    """
    captured_job_ids: list[str] = []
    pool_mock = object()

    async def _mock_present(pool, job_id):
        captured_job_ids.append(job_id)
        return True

    monkeypatch.setattr(redis_module, "get_redis_pool", AsyncMock(return_value=pool_mock))
    monkeypatch.setattr(arq_runtime_module, "arq_job_present_async", _mock_present)

    task_name = "review_project_5_fallback"
    await is_arq_job_possibly_inflight(
        {"queue_mode": "arq"},  # 无 arq_job_id
        task_name=task_name,
    )

    assert captured_job_ids == [task_name], (
        f"arq_job_id 缺失时应以 task_name='{task_name}' 作为兜底 job_id，"
        f"实际传入: {captured_job_ids}"
    )


async def test_a6_get_redis_pool_exception_returns_true_conservative(monkeypatch):
    """场景6：get_redis_pool 抛异常 → 保守返回 True（与 has_confirmed_pending_arq_job 方向相反）。

    has_confirmed_pending_arq_job 异常时返回 False（不挽救），
    is_arq_job_possibly_inflight 异常时返回 True（放弃本轮恢复），两者保守方向相反。
    """
    monkeypatch.setattr(
        redis_module,
        "get_redis_pool",
        AsyncMock(side_effect=RuntimeError("redis 连接超时")),
    )

    result = await is_arq_job_possibly_inflight(
        {"queue_mode": "arq", "arq_job_id": "job-err1"}, task_name="review_project_6"
    )

    assert result is True, (
        "get_redis_pool 抛异常时应保守视为在途并返回 True，"
        "因为误重投代价远高于晚恢复一轮"
    )


async def test_a7_arq_job_present_async_exception_returns_true_conservative(monkeypatch):
    """场景7：arq_job_present_async 抛异常 → 保守返回 True。"""
    pool_mock = object()
    monkeypatch.setattr(redis_module, "get_redis_pool", AsyncMock(return_value=pool_mock))
    monkeypatch.setattr(
        arq_runtime_module,
        "arq_job_present_async",
        AsyncMock(side_effect=ConnectionError("redis 连接断开")),
    )

    result = await is_arq_job_possibly_inflight(
        {"queue_mode": "arq", "arq_job_id": "job-err2"}, task_name="review_project_7"
    )

    assert result is True, (
        "arq_job_present_async 抛异常时应保守视为在途并返回 True"
    )


@pytest.mark.parametrize(
    ("task_name", "arq_function", "job_id"),
    [
        ("review_project_3654", "review_project_job", "job-loop-stall-review"),
        ("process_document_3654", "process_document_job", "job-loop-stall-process"),
    ],
)
async def test_a8b_worker_gap_unconfirmed_keeps_inflight(
    monkeypatch, task_name, arq_function, job_id
):
    """：痕迹仍在且 worker 缺失未确认 → 视为在途，禁止一票放行。

 问题形态：事件循环被合法堵塞导致存活键过期，不等于执行体消失。
 夹具1 review_project_job；夹具2 process_document_job。
 """
    pool_mock = object()
    monkeypatch.setattr(redis_module, "get_redis_pool", AsyncMock(return_value=pool_mock))
    monkeypatch.setattr(
        arq_runtime_module, "arq_job_present_async", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        arq_runtime_module, "has_alive_worker_for", Mock(return_value=False)
    )
    monkeypatch.setattr(
        lifecycle_module, "is_worker_confirmed_missing", Mock(return_value=False)
    )

    result = await is_arq_job_possibly_inflight(
        {"queue_mode": "arq", "arq_job_id": job_id},
        task_name=task_name,
        arq_function=arq_function,
    )

    assert result is True, (
        "worker 存活键瞬时过期不得把仍有 ARQ 痕迹的 job 判成不在途"
    )


@pytest.mark.parametrize(
    ("task_name", "arq_function", "job_id"),
    [
        ("writing_extract_99", "writing_extract_job", "job-stale-ttl"),
        ("review_project_99", "review_project_job", "job-stale-ttl-review"),
    ],
)
async def test_a8_worker_dead_past_no_progress_window_allows_recovery(
    monkeypatch, task_name, arq_function, job_id
):
    """场景8：痕迹存在、worker 缺失已确认且越过无进展窗 → 允许恢复。

 抬高 job_timeout 后 in_progress 残留窗口变长；确认无执行体且业务心跳停摆后
 残留键不能挡恢复。夹具1 writing_extract_job；夹具2 review_project_job。
 """
    pool_mock = object()
    monkeypatch.setattr(redis_module, "get_redis_pool", AsyncMock(return_value=pool_mock))
    monkeypatch.setattr(
        arq_runtime_module, "arq_job_present_async", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        arq_runtime_module, "has_alive_worker_for", Mock(return_value=False)
    )
    monkeypatch.setattr(
        lifecycle_module, "is_worker_confirmed_missing", Mock(return_value=True)
    )

    now = time.time()
    result = await is_arq_job_possibly_inflight(
        {
            "queue_mode": "arq",
            "arq_job_id": job_id,
            "last_heartbeat": now - 1900,
        },
        task_name=task_name,
        arq_function=arq_function,
    )

    assert result is False, (
        "worker 缺失已确认且心跳越过无进展窗时即使 in-progress 残留也应允许恢复"
    )


@pytest.mark.parametrize(
    ("task_name", "arq_function", "job_id"),
    [
        ("review_project_3654", "review_project_job", "job-short-gap-review"),
        ("writing_compose_3654", "writing_compose_job", "job-short-gap-compose"),
    ],
)
async def test_a8c_worker_missing_within_window_still_inflight(
    monkeypatch, task_name, arq_function, job_id
):
    """：确认缺失但心跳未越无进展窗 → 仍视为在途，禁止重投。"""
    pool_mock = object()
    monkeypatch.setattr(redis_module, "get_redis_pool", AsyncMock(return_value=pool_mock))
    monkeypatch.setattr(
        arq_runtime_module, "arq_job_present_async", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        arq_runtime_module, "has_alive_worker_for", Mock(return_value=False)
    )
    monkeypatch.setattr(
        lifecycle_module, "is_worker_confirmed_missing", Mock(return_value=True)
    )

    result = await is_arq_job_possibly_inflight(
        {
            "queue_mode": "arq",
            "arq_job_id": job_id,
            "last_heartbeat": time.time() - 200,
        },
        task_name=task_name,
        arq_function=arq_function,
    )

    assert result is True, (
        "存活键过期不得把未越无进展窗的 traces 判成不在途"
    )


async def test_a9_worker_alive_with_in_progress_still_blocks_recovery(monkeypatch):
    """场景9：痕迹存在且 worker 存活 → 仍视为在途，阻止重投。"""
    pool_mock = object()
    monkeypatch.setattr(redis_module, "get_redis_pool", AsyncMock(return_value=pool_mock))
    monkeypatch.setattr(
        arq_runtime_module, "arq_job_present_async", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        arq_runtime_module, "has_alive_worker_for", Mock(return_value=True)
    )

    result = await is_arq_job_possibly_inflight(
        {"queue_mode": "arq", "arq_job_id": "job-live"},
        task_name="review_project_9",
        arq_function="review_project_job",
    )

    assert result is True


# ============================================================
# B. review_service 恢复守卫集成测试（场景 8-9）
# ============================================================


async def test_b8_review_recovery_guard_hit_skips_dispatch_cancel_increment(monkeypatch):
    """场景8：守卫命中（is_arq_job_possibly_inflight=True）时：
    - dispatch_background_job 不被调用
    - cancel_task 不被调用
    - increment_recovery_count 不被调用（不消耗恢复配额）
    """
    project_id = 2849
    heartbeat = {"queue_mode": "arq", "arq_job_id": "job-inflight-849"}
    project = _make_project(project_id=project_id)
    session = _FakeServiceSession(
        _make_review_count_responses(project=project, total=5, extracted=2, reviewed=0)
    )

    dispatch_calls: list = []
    cancel_calls: list = []
    increment_calls: list = []

    _install_review_recovery_stubs(
        monkeypatch,
        recovery_count=0,
        heartbeat=heartbeat,
        terminal_snapshot=None,
        dispatch_calls=dispatch_calls,
        cancel_calls=cancel_calls,
        increment_calls=increment_calls,
        guard_return=True,  # 守卫命中
    )

    await _call_get_status(session, project)

    assert dispatch_calls == [], (
        "守卫命中时 dispatch_background_job 不应被调用，"
        "旧 job 仍在途重投会触发 uq_review_project_requirement 唯一约束冲突"
    )
    assert cancel_calls == [], (
        "守卫命中时 cancel_task 不应被调用"
    )
    assert increment_calls == [], (
        "守卫命中时 increment_recovery_count 不应被调用，"
        "在途等待不应白白消耗恢复配额"
    )


async def test_b9_review_recovery_guard_miss_dispatches_normally(monkeypatch):
    """场景9：守卫未命中（is_arq_job_possibly_inflight=False）时：
    恢复链路照常执行，dispatch_background_job 被调用一次。
    """
    project_id = 2849
    heartbeat = {"queue_mode": "arq", "arq_job_id": "job-already-gone"}
    project = _make_project(project_id=project_id)
    session = _FakeServiceSession(
        _make_review_count_responses(project=project, total=5, extracted=2, reviewed=0)
    )

    dispatch_calls: list = []

    _install_review_recovery_stubs(
        monkeypatch,
        recovery_count=0,
        heartbeat=heartbeat,
        terminal_snapshot=None,
        dispatch_calls=dispatch_calls,
        guard_return=False,  # 守卫未命中，照常恢复
    )

    await _call_get_status(session, project)

    assert len(dispatch_calls) == 1, (
        f"守卫未命中时应 dispatch 一次自动恢复任务，实际 dispatch {len(dispatch_calls)} 次"
    )


# ============================================================
# C. collusion 恢复守卫接线静态验证（场景 10）
# ============================================================


def test_c10_collusion_recovery_guard_structure_exists_statically():
    """场景10（静态断言）：验证 compute_collusion_status 含守卫调用与 else 分支。

    降级原因：compute_collusion_status 依赖 12+ 次 execute（Project / Document /
    CollusionExtraction / CollusionResult 等 ORM 对象）和权限校验、心跳超时计算，
    构造 fake session 成本过高；改用 inspect.getsource 锁定关键结构不变性：
    1. 守卫被 import 并以 await 方式调用
    2. 守卫调用后有 else 分支（防止守卫命中时仍执行 increment/dispatch）
    """
    import app.services.collusion_status_service as collusion_module

    # 取函数源码（compute_collusion_status 是模块级函数，不是方法）
    source = inspect.getsource(collusion_module.compute_collusion_status)

    # 1. 守卫被 import
    assert "is_arq_job_possibly_inflight" in source, (
        "compute_collusion_status 源码中未找到 is_arq_job_possibly_inflight，"
        "守卫可能已被移除"
    )

    # 2. 守卫以 await 方式调用（非同步调用）
    assert "await is_arq_job_possibly_inflight(" in source, (
        "is_arq_job_possibly_inflight 未以 await 方式调用，守卫可能未正确接线"
    )

    # 3. 守卫调用后存在 else 分支（防止守卫命中时仍执行恢复链路）
    guard_idx = source.index("await is_arq_job_possibly_inflight(")
    post_guard_source = source[guard_idx:]
    assert "else:" in post_guard_source, (
        "is_arq_job_possibly_inflight 调用后未找到 else 分支，"
        "守卫命中时恢复链路可能仍会执行（缺少互斥保护）"
    )

    # 4. else 分支内存在 increment_recovery_count（正常恢复路径）
    else_idx = post_guard_source.index("else:")
    post_else_source = post_guard_source[else_idx:]
    assert "increment_recovery_count" in post_else_source, (
        "else 分支中未找到 increment_recovery_count，"
        "正常恢复路径可能未接线"
    )

    # 5. else 分支内存在 dispatch_background_job（正常恢复路径）
    assert "dispatch_background_job" in post_else_source, (
        "else 分支中未找到 dispatch_background_job，"
        "正常恢复路径可能未接线"
    )
