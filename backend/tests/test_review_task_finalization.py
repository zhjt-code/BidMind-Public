"""防御测试：投标审查任务收尾事务隔离与状态收敛。

覆盖以下根因修复点：

1. `run_review_task` 步骤 5 的"项目完成态提交 + 计费结算"已拆入独立
   session；当 `BillingGuard.on_completed` 抛错时，项目仍能写成 COMPLETED。
2. `run_review_task` 外层异常分支会在 aborted 主事务上执行 rollback，
   并使用全新 session 调用 `_fail_project` 将项目写成 FAILED。
3. `ReviewService.get_review_status` 在"结果已就绪但 project.status 仍
   REVIEWING、task 不活跃"时，不再触发重投，而是自动补写 COMPLETED。

测试使用 SimpleNamespace + fake session 上下文，避免触碰真实数据库，
与 `tests/test_task_binding_release.py` 的风格对齐。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.tasks import review_task as review_task_module
from app.models.project import ProjectStatus
from app.services import review_service as review_service_module


class _FakeSession:
    """仅暴露 commit/rollback，配合 monkey-patched _get_project/_get_document 使用。"""

    def __init__(self, index: int, recorder: list[dict[str, Any]]) -> None:
        self._index = index
        self._recorder = recorder

    async def commit(self) -> None:
        self._recorder.append({"session": self._index, "action": "commit"})

    async def rollback(self) -> None:
        self._recorder.append({"session": self._index, "action": "rollback"})


class _SessionContext:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


def _make_session_factory(recorder: list[dict[str, Any]]):
    """按顺序为每次 async_session_factory() 调用发一个独立 session，便于按序断言。"""

    counter = {"idx": 0}

    def _factory() -> _SessionContext:
        counter["idx"] += 1
        idx = counter["idx"]
        session = _FakeSession(idx, recorder)
        recorder.append({"session": idx, "action": "open"})
        return _SessionContext(session)

    return _factory


class _FakeExtractor:
    async def extract_for_project(self, **_: Any) -> list[Any]:
        return ["bid-response-1"]


class _FakeMatcher:
    async def match_project(self, *_: Any, **__: Any) -> list[Any]:
        return [SimpleNamespace(requirement_id=1)]

    def summarize_matches(self, _pairs: list[Any]) -> dict[str, int]:
        return {"total": 1, "with_response": 1, "without_response": 0}


class _FakeOrchestrator:
    async def review_project(self, **_: Any) -> list[Any]:
        return ["review-1"]


class _FailingOrchestrator:
    async def review_project(self, **_: Any) -> list[Any]:
        raise RuntimeError("simulated review failure")


def _install_common_monkeypatches(
    monkeypatch: pytest.MonkeyPatch,
    project: SimpleNamespace,
    recorder: list[dict[str, Any]],
    *,
    orchestrator: Any,
    extractor: Any = None,
    matcher: Any = None,
) -> None:
    """注入 run_review_task 所需的最小依赖，只保留我们关心的行为。"""

    monkeypatch.setattr(
        review_task_module,
        "async_session_factory",
        _make_session_factory(recorder),
    )

    async def _fake_get_project(_db, project_id):
        assert project_id == project.id
        return project

    async def _fake_get_document(_db, project_id, doc_type):
        return SimpleNamespace(id=(1000 + doc_type.value.__hash__() % 100))

    monkeypatch.setattr(review_task_module, "_get_project", _fake_get_project)
    monkeypatch.setattr(review_task_module, "_get_document", _fake_get_document)

    async def _fake_load_bid_envelope(_db, project_id):
        assert project_id == project.id
        return SimpleNamespace(has_documents=True, is_complete=True, document_ids=[2001])

    import app.core.bid_envelope as bid_envelope_module
    monkeypatch.setattr(bid_envelope_module, "load_bid_envelope", _fake_load_bid_envelope)

    async def _fake_qa_current(_project_id):
        return "disabled"

    import app.services.document_qa.ensure_current as ensure_current_module
    monkeypatch.setattr(ensure_current_module, "ensure_document_qa_current", _fake_qa_current)

    monkeypatch.setattr(
        review_task_module,
        "get_bid_extractor",
        lambda db, concurrency, project_id: extractor or _FakeExtractor(),
    )
    monkeypatch.setattr(
        review_task_module,
        "get_requirement_matcher",
        lambda db: matcher or _FakeMatcher(),
    )
    monkeypatch.setattr(
        review_task_module,
        "get_review_orchestrator",
        lambda db, project_id: orchestrator,
    )

    # 心跳/取消/日志上下文：无关业务的静默化
    import app.core.tasks as tasks_module
    monkeypatch.setattr(
        tasks_module, "update_heartbeat", lambda *args, **kwargs: None
    )
    import app.core.arq_runtime as arq_module
    monkeypatch.setattr(arq_module, "is_project_cancelled", lambda _pid: False)
    monkeypatch.setattr(
        arq_module, "raise_if_project_cancelled", lambda _pid: None
    )

    async def _noop_release(_project_id: int) -> None:
        return None

    import app.core.ai as ai_module
    monkeypatch.setattr(ai_module, "release_project_binding", _noop_release)


def _actions_for(recorder: list[dict[str, Any]], session_idx: int) -> list[str]:
    return [e["action"] for e in recorder if e["session"] == session_idx]


async def test_run_review_task_completed_survives_billing_failure(monkeypatch):
    """S2 防御：计费结算抛错时，项目仍应写入 COMPLETED 并不污染事务。

 后契约再次更新：步骤 4 不再外层包 async_session_factory,
 编排器内部用 db_session_scope 各自申请短 session, 不被 async_session_factory
 的 recorder 计数。所以最少应打开 5 个 session：
 step1 / step2 / step3 / status / billing。
 """
    project = SimpleNamespace(
        id=9001,
        status=ProjectStatus.REVIEWING,
        error_message="残留旧错误",
        billing_mode="paid",
        billing_hold_id=123,
    )
    recorder: list[dict[str, Any]] = []

    _install_common_monkeypatches(
        monkeypatch, project, recorder, orchestrator=_FakeOrchestrator()
    )

    async def _raise_on_completed(_db, _project):
        raise RuntimeError("simulated billing failure")

    from app.billing.services import billing_guard as billing_guard_module
    monkeypatch.setattr(
        billing_guard_module.BillingGuard,
        "on_completed",
        staticmethod(_raise_on_completed),
    )

    await review_task_module.run_review_task(project.id)

    assert project.status == ProjectStatus.COMPLETED, (
        "计费异常不得反噬项目完成态"
    )
    assert project.error_message is None

    # 步骤 1-3 各自独立 session + 5.1 status + 5.2 billing
    # (步骤 4 外层 session 已去除, 由编排器内部 db_session_scope 自管)
    opens = [e["session"] for e in recorder if e["action"] == "open"]
    assert len(opens) >= 5, (
        f"期望步骤1-3 + status + billing 至少 5 个 session, 实际={opens}"
    )

    # 关键行为断言（不依赖具体 session 编号）：
    # 1) status session（倒数第 2 次开的）必须 commit -- 写入 COMPLETED 主事实
    status_session_idx = opens[-2]
    assert "commit" in _actions_for(recorder, status_session_idx), (
        f"status session={status_session_idx} 必须 commit COMPLETED"
    )
    # 2) billing session（最后一次开的）必须 rollback -- 计费异常被吞
    billing_session_idx = opens[-1]
    assert "rollback" in _actions_for(recorder, billing_session_idx), (
        f"billing session={billing_session_idx} 必须 rollback"
    )


async def test_run_review_task_failure_uses_fresh_session(monkeypatch):
    """S3 防御：审查异常时，必须在独立干净 session 中写入 FAILED。

 后契约再次更新：步骤 4 外层 session 已去除, 但步骤 1-3 仍各自
 独立短 session, 异常路径仍要在全新 err_db 中写 FAILED。
 """
    project = SimpleNamespace(
        id=9002,
        status=ProjectStatus.REVIEWING,
        error_message=None,
        billing_mode="paid",
        billing_hold_id=456,
    )
    recorder: list[dict[str, Any]] = []

    _install_common_monkeypatches(
        monkeypatch, project, recorder, orchestrator=_FailingOrchestrator()
    )

    async def _noop_on_failed(_db, _project):
        return None

    from app.billing.services import billing_guard as billing_guard_module
    monkeypatch.setattr(
        billing_guard_module.BillingGuard,
        "on_failed",
        staticmethod(_noop_on_failed),
    )

    await review_task_module.run_review_task(project.id)

    assert project.status == ProjectStatus.FAILED, "异常路径必须把项目写成 FAILED"
    assert project.error_message and "系统处理异常" in project.error_message

    opens = [e["session"] for e in recorder if e["action"] == "open"]
    # 步骤 1-3 各自一个 session + 失败兜底 err_db, 至少 4 个
    # (步骤 4 外层 session 已去除, 编排器内部 db_session_scope 自管)
    assert len(opens) >= 4, (
        f"期望步骤1-3 + err_db 至少 4 个 session, 实际={opens}"
    )
    # err session（最后一次打开的）必须由 _fail_project 触发 commit
    err_session_idx = opens[-1]
    assert "commit" in _actions_for(recorder, err_session_idx), (
        f"err session={err_session_idx} 必须 commit FAILED"
    )


# ── 分步特征测试: 锁定各步骤的提前返回与心跳顺序, 编排拆分不得改变 ──


class _ValueErrorExtractor:
    async def extract_for_project(self, **_: Any) -> list[Any]:
        raise ValueError("没有可提取的要求")


class _CrashingExtractor:
    async def extract_for_project(self, **_: Any) -> list[Any]:
        raise RuntimeError("simulated extract crash")


class _ValueErrorMatcher(_FakeMatcher):
    async def match_project(self, *_: Any, **__: Any) -> list[Any]:
        raise ValueError("no confirmed requirements")


class _RecordingExtractor(_FakeExtractor):
    def __init__(self) -> None:
        self.calls = 0

    async def extract_for_project(self, **kwargs: Any) -> list[Any]:
        self.calls += 1
        return await super().extract_for_project(**kwargs)


class _RecordingMatcher(_FakeMatcher):
    def __init__(self) -> None:
        self.calls = 0

    async def match_project(self, *args: Any, **kwargs: Any) -> list[Any]:
        self.calls += 1
        return await super().match_project(*args, **kwargs)


class _RecordingOrchestrator(_FakeOrchestrator):
    def __init__(self) -> None:
        self.calls = 0

    async def review_project(self, **kwargs: Any) -> list[Any]:
        self.calls += 1
        return await super().review_project(**kwargs)


class _CancellingOrchestrator:
    async def review_project(self, **_: Any) -> list[Any]:
        from app.core.arq_runtime import ProjectCancelledError

        raise ProjectCancelledError(9106, "deleted")


def _make_project(project_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=project_id,
        status=ProjectStatus.REVIEWING,
        error_message=None,
        billing_mode="paid",
        billing_hold_id=1,
    )


def _silence_billing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(_db, _project):
        return None

    from app.billing.services import billing_guard as billing_guard_module

    monkeypatch.setattr(billing_guard_module.BillingGuard, "on_failed", staticmethod(_noop))
    monkeypatch.setattr(billing_guard_module.BillingGuard, "on_completed", staticmethod(_noop))


def _capture_stages(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    stages: list[str] = []
    import app.core.tasks as tasks_module

    monkeypatch.setattr(
        tasks_module,
        "update_heartbeat",
        lambda name, stage="", progress="", extra=None: stages.append(stage),
    )
    return stages


async def test_run_review_task_success_walks_steps_in_order(monkeypatch):
    """成功路径心跳阶段顺序固定: 启动 -> 步骤1..5。"""
    project = _make_project(9101)
    recorder: list[dict[str, Any]] = []
    _install_common_monkeypatches(
        monkeypatch, project, recorder, orchestrator=_FakeOrchestrator()
    )
    _silence_billing(monkeypatch)
    stages = _capture_stages(monkeypatch)

    await review_task_module.run_review_task(project.id)

    assert project.status == ProjectStatus.COMPLETED
    assert stages == ["启动", "步骤1", "步骤2", "步骤3", "步骤3", "步骤4", "步骤5"]


async def test_run_review_task_incomplete_envelope_stops_before_extract(monkeypatch):
    project = _make_project(9102)
    recorder: list[dict[str, Any]] = []
    extractor = _RecordingExtractor()
    _install_common_monkeypatches(
        monkeypatch, project, recorder,
        orchestrator=_FakeOrchestrator(), extractor=extractor,
    )
    _silence_billing(monkeypatch)

    async def _incomplete(_db, _project_id):
        return SimpleNamespace(has_documents=True, is_complete=False, document_ids=[2001])

    import app.core.bid_envelope as bid_envelope_module
    monkeypatch.setattr(bid_envelope_module, "load_bid_envelope", _incomplete)

    await review_task_module.run_review_task(project.id)

    assert project.status == ProjectStatus.FAILED
    assert "未齐套" in project.error_message
    assert extractor.calls == 0


async def test_run_review_task_extract_value_error_stops_before_match(monkeypatch):
    project = _make_project(9103)
    recorder: list[dict[str, Any]] = []
    matcher = _RecordingMatcher()
    _install_common_monkeypatches(
        monkeypatch, project, recorder,
        orchestrator=_FakeOrchestrator(), extractor=_ValueErrorExtractor(), matcher=matcher,
    )
    _silence_billing(monkeypatch)

    await review_task_module.run_review_task(project.id)

    assert project.status == ProjectStatus.FAILED
    assert project.error_message.startswith("投标响应提取失败")
    assert matcher.calls == 0


async def test_run_review_task_extract_crash_fails_in_fresh_session(monkeypatch):
    project = _make_project(9104)
    recorder: list[dict[str, Any]] = []
    matcher = _RecordingMatcher()
    _install_common_monkeypatches(
        monkeypatch, project, recorder,
        orchestrator=_FakeOrchestrator(), extractor=_CrashingExtractor(), matcher=matcher,
    )
    _silence_billing(monkeypatch)

    await review_task_module.run_review_task(project.id)

    assert project.status == ProjectStatus.FAILED
    assert project.error_message.startswith("投标响应提取异常")
    assert matcher.calls == 0
    opens = [e["session"] for e in recorder if e["action"] == "open"]
    assert "rollback" in _actions_for(recorder, opens[1]), "提取 session 必须回滚"
    assert "commit" in _actions_for(recorder, opens[2]), "失败态必须在新 session 提交"


async def test_run_review_task_match_value_error_stops_before_review(monkeypatch):
    project = _make_project(9105)
    recorder: list[dict[str, Any]] = []
    orchestrator = _RecordingOrchestrator()
    _install_common_monkeypatches(
        monkeypatch, project, recorder,
        orchestrator=orchestrator, matcher=_ValueErrorMatcher(),
    )
    _silence_billing(monkeypatch)

    await review_task_module.run_review_task(project.id)

    assert project.status == ProjectStatus.FAILED
    assert "未找到已确认的招标要求" in project.error_message
    assert orchestrator.calls == 0


async def test_run_review_task_project_cancelled_is_not_failure(monkeypatch):
    project = _make_project(9106)
    recorder: list[dict[str, Any]] = []
    _install_common_monkeypatches(
        monkeypatch, project, recorder, orchestrator=_CancellingOrchestrator()
    )
    _silence_billing(monkeypatch)
    stages = _capture_stages(monkeypatch)

    await review_task_module.run_review_task(project.id)

    assert project.status == ProjectStatus.REVIEWING
    assert stages[-1] == "cancelled"


async def test_run_review_task_does_not_wait_for_document_qa_backfill(monkeypatch):
    """: 质检当前性补算永不结束时, 审查仍走完五步并写 COMPLETED。"""
    project = _make_project(9107)
    recorder: list[dict[str, Any]] = []
    _install_common_monkeypatches(
        monkeypatch, project, recorder, orchestrator=_FakeOrchestrator()
    )
    _silence_billing(monkeypatch)

    import app.services.document_qa.ensure_current as ensure_current_module

    never = asyncio.Event()

    async def _endless_backfill(_project_id):
        await never.wait()
        return "completed"

    monkeypatch.setattr(ensure_current_module, "ensure_document_qa_current", _endless_backfill)
    try:
        await asyncio.wait_for(review_task_module.run_review_task(project.id), timeout=5)
    finally:
        for task in list(ensure_current_module._DETACHED_CURRENCY):
            task.cancel()

    assert project.status == ProjectStatus.COMPLETED


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
    """为 ReviewService.get_review_status 定制的最小 DB 桩。

    通过响应队列按调用顺序返回不同的 execute 结果，避免依赖真实 SQL 引擎。
    """

    def __init__(self, responses: list[_FakeExecResult]) -> None:
        self._responses = responses
        self._idx = 0
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, _stmt):
        if self._idx >= len(self._responses):
            raise AssertionError("execute() 调用次数超过测试准备的响应队列")
        value = self._responses[self._idx]
        self._idx += 1
        return value

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


async def test_get_review_status_converges_when_results_ready(monkeypatch):
    """S4 防御：review_results 已写满但 status 仍 REVIEWING 时，
    不应触发重投，而应把 project.status 自动补写为 COMPLETED。"""
    project = SimpleNamespace(
        id=9003,
        status=ProjectStatus.REVIEWING,
        error_message=None,
        owner_id=1,
        billing_mode="paid",
        billing_hold_id=789,
    )

    # get_review_status 的 execute 调用顺序：
    # 1) _get_project_or_raise -> scalar_one_or_none 返回 project
    # 2) tender_doc 查询 -> 返回 tender_doc
    # 3) requirements 查询 -> 返回 3 条已确认要求
    # 4) BidResponse count -> 返回 3
    # 5) ReviewResult count -> 返回 3
    tender_doc = SimpleNamespace(id=501)
    requirements = [
        SimpleNamespace(id=11),
        SimpleNamespace(id=12),
        SimpleNamespace(id=13),
    ]
    responses = [
        _FakeExecResult(scalar_one_value=project),
        _FakeExecResult(scalar_one_value=tender_doc),
        _FakeExecResult(scalars_value=requirements),
        _FakeExecResult(scalar_value=3),
        _FakeExecResult(scalar_value=3),
    ]
    session = _FakeServiceSession(responses)

    # 关键 stub：任务不活跃、不在宽限、心跳空；避免触发自动恢复分支
    # 注: 历史 stub `is_task_active` / `is_task_in_enqueue_grace` 在 status 接口
    # 升级到 `inspect_task_liveness` 后不再被引用; 此处直接构造 TaskLiveness。
    from app.core.tasks import TaskLiveness as _TaskLiveness

    _missing_liveness = _TaskLiveness(
        task_name="review_project_test",
        state="missing",
        active=False,
        heartbeat=None,
        stage="",
        age_seconds=None,
        reason="test_missing",
    )
    monkeypatch.setattr(
        review_service_module, "inspect_task_liveness", lambda *_a, **_k: _missing_liveness
    )
    monkeypatch.setattr(
        review_service_module, "get_task_heartbeat", lambda _n: None
    )
    monkeypatch.setattr(review_service_module, "get_recovery_count", lambda _n: 0)

    enqueue_calls: list[tuple] = []

    async def _capture_enqueue(*args, **kwargs):
        enqueue_calls.append((args, kwargs))
        return "job-id"

    monkeypatch.setattr(
        "app.core.arq_runtime.enqueue_job_safe",
        _capture_enqueue,
    )

    async def _noop_on_completed(_db, _project):
        return None

    from app.billing.services import billing_guard as billing_guard_module
    monkeypatch.setattr(
        billing_guard_module.BillingGuard,
        "on_completed",
        staticmethod(_noop_on_completed),
    )

    service = review_service_module.ReviewService(session)  # type: ignore[arg-type]
    user = SimpleNamespace(id=project.owner_id, role="ADMIN")
    status = await service.get_review_status(project.id, user)  # type: ignore[arg-type]

    assert project.status == ProjectStatus.COMPLETED, (
        "结果已就绪时应补写 COMPLETED 而非重投任务"
    )
    assert status.overall_status == "completed"
    assert session.commits >= 1, "补写 COMPLETED 必须触发 commit"
    assert enqueue_calls == [], "不得触发 review_project_job 重投"
