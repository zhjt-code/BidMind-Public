"""任务契约统一收口: dispatch_background_job + collusion stall snapshot 回归测试

覆盖目标:
  - dispatch_background_job 在 ARQ 可用时走入队、不可用时回退 asyncio,
    两种情况下都写入一条 queued 心跳, 与 mark_task_queued 语义对齐。
  - ARQ 回退路径调用 fallback_factory() 且传入统一任务参数;
    禁止业务层在 fallback 分支另起旁路实现。
  - collusion stall snapshot 读写接口: 有 Redis 时优先 Redis,
    缺失时降级本地字典, 且语义一致。
  - writing materials 批处理触发器不再在请求内执行长流程 (仅分派后立即返回).
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.arq_runtime import ArqDispatchResult
from app.core.tasks import (
    TASK_STAGE_QUEUED,
    cleanup_task,
    clear_collusion_cache,
    dispatch_background_job,
    get_task_heartbeat,
    read_collusion_stall_snapshot,
    write_collusion_stall_snapshot,
)


@asynccontextmanager
async def _granted_fallback_permit(*_args: Any, **_kwargs: Any):
    yield True


# ---------------------------------------------------------------------------
# dispatch_background_job 契约
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_uses_arq_when_available_and_marks_queued() -> None:
    """当 enqueue_job_safe 返回 job_id 时, 不得创建本地 asyncio 任务,
    且应在 Redis/心跳端写入一条 queued 心跳, stage=queued, queue_mode=arq."""
    task_name = "pytest_dispatch_arq_case"
    cleanup_task(task_name)
    fallback_invoked = False

    async def _fallback() -> None:
        nonlocal fallback_invoked
        fallback_invoked = True

    # dispatch_background_job 内部通过 `from app.core.arq_runtime import enqueue_job_safe`
    # 延迟导入, 因此必须 patch 源模块中的符号, 而非 app.core.tasks 下的别名。
    mock_enqueue = AsyncMock(
        return_value=ArqDispatchResult(
            queued=True,
            function_name="writing_material_process_job",
            job_id="arq-job-test-123",
            reason="queued",
            queue_name="arq:queue:process",
        )
    )
    with (
        patch("app.core.arq_runtime.enqueue_job_detailed", mock_enqueue),
        patch("app.core.tasks.resource_permit.fallback_permit_context", _granted_fallback_permit),
    ):
        queue_mode, arq_job_id = await dispatch_background_job(
            task_name=task_name,
            arq_function="writing_material_process_job",
            fallback_factory=lambda: _fallback(),
            task_type="writing_material_process",
            project_id=9001,
            arq_args=(9001,),
        )

    assert queue_mode == "arq"
    assert arq_job_id == "arq-job-test-123"
    assert fallback_invoked is False, "ARQ 入队成功时不得调用 fallback"
    mock_enqueue.assert_awaited_once_with("writing_material_process_job", 9001)

    heartbeat = get_task_heartbeat(task_name)
    # ARQ 模式只写 Redis; 本机测试环境 Redis 可能缺失, 心跳可能为 None,
    # 但必须不在本进程内存中留下 stage!="queued" 的脏数据
    if heartbeat is not None:
        assert heartbeat.get("stage") == TASK_STAGE_QUEUED
        assert heartbeat.get("queue_mode") == "arq"

    cleanup_task(task_name)


@pytest.mark.asyncio
async def test_dispatch_fails_loud_when_arq_configured_but_enqueue_errors() -> None:
    """: ARQ 已配置但入队失败时, 永不挪到 API 进程。

 等待窗耗尽后, dispatch_background_job 写终态 failed 心跳, 返回
 queue_mode=worker_unavailable, 不调用 fallback_factory。
 """
    task_name = "pytest_dispatch_worker_unavailable_case"
    cleanup_task(task_name)
    fallback_invoked = False

    async def _fallback() -> None:
        nonlocal fallback_invoked
        fallback_invoked = True

    # 反复入队都失败, reason 保持 enqueue_error
    mock_enqueue = AsyncMock(
        return_value=ArqDispatchResult(
            queued=False,
            function_name="writing_material_process_job",
            reason="enqueue_error",
            queue_name="arq:queue:process",
            error="boom",
        )
    )
    with (
        patch("app.core.arq_runtime.enqueue_job_detailed", mock_enqueue),
        patch("app.core.tasks.resource_permit.fallback_permit_context", _granted_fallback_permit),
        patch("asyncio.sleep", AsyncMock()),
        patch("app.core.config.settings.WORKER_WAIT_BEFORE_FALLBACK_SEC", 0.1),
        patch("app.core.config.settings.BACKGROUND_FALLBACK_ENABLED", False),
    ):
        queue_mode, arq_job_id = await dispatch_background_job(
            task_name=task_name,
            arq_function="writing_material_process_job",
            fallback_factory=lambda: _fallback(),
            task_type="writing_material_process",
            project_id=9002,
            arq_args=(9002,),
        )

    assert queue_mode == "worker_unavailable"
    assert arq_job_id is None
    assert fallback_invoked is False, "ARQ 已配置但入队失败时不得调用 fallback"
    assert mock_enqueue.await_count >= 1

    heartbeat = get_task_heartbeat(task_name)
    assert heartbeat is not None
    assert heartbeat.get("stage") == "failed"
    progress = str(heartbeat.get("progress", ""))
    assert "enqueue_error" in progress
    assert "Worker 不可用" in progress

    cleanup_task(task_name)


@pytest.mark.asyncio
async def test_dispatch_prefer_arq_disabled_uses_in_process_fallback() -> None:
    """形态 A 开发模式: prefer_arq_disabled 且 BACKGROUND_FALLBACK_ENABLED=true 时,
    仍允许同进程执行 (开发本机无 worker)。
    """
    task_name = "pytest_dispatch_prefer_disabled_case"
    cleanup_task(task_name)
    fallback_invoked_event = asyncio.Event()

    async def _fallback() -> None:
        fallback_invoked_event.set()
        return None

    mock_enqueue = AsyncMock(
        return_value=ArqDispatchResult(
            queued=False,
            function_name="writing_material_process_job",
            reason="prefer_arq_disabled",
            queue_name=None,
        )
    )
    with (
        patch("app.core.arq_runtime.enqueue_job_detailed", mock_enqueue),
        patch("app.core.tasks.resource_permit.fallback_permit_context", _granted_fallback_permit),
        patch("app.core.config.settings.BACKGROUND_FALLBACK_ENABLED", True),
    ):
        queue_mode, arq_job_id = await dispatch_background_job(
            task_name=task_name,
            arq_function="writing_material_process_job",
            fallback_factory=lambda: _fallback(),
            task_type="writing_material_process",
            project_id=9101,
            arq_args=(9101,),
        )
        try:
            await asyncio.wait_for(fallback_invoked_event.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pytest.fail("fallback 协程未执行 (开发模式应走 asyncio)")

    assert queue_mode == "asyncio_fallback"
    assert arq_job_id is None

    cleanup_task(task_name)


@pytest.mark.asyncio
async def test_dispatch_prefer_arq_disabled_with_flag_off_fails_loud() -> None:
    """形态 A 误配: prefer_arq_disabled + BACKGROUND_FALLBACK_ENABLED=false 时,
    必须明确失败 (不自旋), 这是双重防线。
    """
    task_name = "pytest_dispatch_prefer_disabled_off_case"
    cleanup_task(task_name)
    fallback_invoked = False

    async def _fallback() -> None:
        nonlocal fallback_invoked
        fallback_invoked = True

    mock_enqueue = AsyncMock(
        return_value=ArqDispatchResult(
            queued=False,
            function_name="writing_material_process_job",
            reason="prefer_arq_disabled",
            queue_name=None,
        )
    )
    with (
        patch("app.core.arq_runtime.enqueue_job_detailed", mock_enqueue),
        patch("app.core.tasks.resource_permit.fallback_permit_context", _granted_fallback_permit),
        patch("app.core.config.settings.BACKGROUND_FALLBACK_ENABLED", False),
    ):
        queue_mode, arq_job_id = await dispatch_background_job(
            task_name=task_name,
            arq_function="writing_material_process_job",
            fallback_factory=lambda: _fallback(),
            task_type="writing_material_process",
            project_id=9102,
            arq_args=(9102,),
        )

    assert queue_mode == "worker_unavailable"
    assert fallback_invoked is False

    heartbeat = get_task_heartbeat(task_name)
    assert heartbeat is not None
    assert heartbeat.get("stage") == "failed"
    assert "prefer_arq_disabled" in str(heartbeat.get("progress", ""))

    cleanup_task(task_name)


@pytest.mark.asyncio
async def test_dispatch_function_worker_missing_eventually_queues() -> None:
    """形态 A 第二实体: function_worker_missing 应走等待路径, 恢复后入队。

    用 collusion_analyze_job 作为非 LLM 工种之外的实体 (与 extract_requirements_job 形态一致)。
    """
    task_name = "pytest_dispatch_function_missing_recover_case"
    cleanup_task(task_name)
    fallback_invoked = False

    async def _fallback() -> None:
        nonlocal fallback_invoked
        fallback_invoked = True

    mock_enqueue = AsyncMock(
        side_effect=[
            ArqDispatchResult(
                queued=False,
                function_name="collusion_analyze_job",
                reason="function_worker_missing",
                queue_name="arq:queue:llm",
            ),
            ArqDispatchResult(
                queued=True,
                function_name="collusion_analyze_job",
                job_id="arq-job-collusion-recover",
                reason="queued",
                queue_name="arq:queue:llm",
            ),
        ]
    )
    with (
        patch("app.core.arq_runtime.enqueue_job_detailed", mock_enqueue),
        patch("asyncio.sleep", AsyncMock()),
        patch("app.core.config.settings.WORKER_WAIT_BEFORE_FALLBACK_SEC", 0.1),
    ):
        queue_mode, arq_job_id = await dispatch_background_job(
            task_name=task_name,
            arq_function="collusion_analyze_job",
            fallback_factory=lambda: _fallback(),
            task_type="collusion",
            project_id=9103,
            arq_args=(9103,),
        )

    assert queue_mode == "arq"
    assert arq_job_id == "arq-job-collusion-recover"
    assert fallback_invoked is False
    assert mock_enqueue.await_count == 2

    cleanup_task(task_name)


@pytest.mark.asyncio
async def test_dispatch_worker_unavailable_after_wait_exhaustion() -> None:
    """形态 A 边界: 等待窗内始终 worker_missing, 最终 worker_unavailable 而非 asyncio。"""
    task_name = "pytest_dispatch_wait_exhausted_case"
    cleanup_task(task_name)
    fallback_invoked = False

    async def _fallback() -> None:
        nonlocal fallback_invoked
        fallback_invoked = True

    mock_enqueue = AsyncMock(
        return_value=ArqDispatchResult(
            queued=False,
            function_name="extract_requirements_job",
            reason="worker_missing",
            queue_name="arq:queue:llm",
        )
    )
    with (
        patch("app.core.arq_runtime.enqueue_job_detailed", mock_enqueue),
        patch("asyncio.sleep", AsyncMock()),
        patch("app.core.config.settings.WORKER_WAIT_BEFORE_FALLBACK_SEC", 0.1),
        patch("app.core.config.settings.BACKGROUND_FALLBACK_ENABLED", False),
    ):
        queue_mode, arq_job_id = await dispatch_background_job(
            task_name=task_name,
            arq_function="extract_requirements_job",
            fallback_factory=lambda: _fallback(),
            task_type="extract",
            project_id=9104,
            arq_args=(9104,),
        )

    assert queue_mode == "worker_unavailable"
    # >= 2 才能证明「等过」: 循环外首投 1 次, 等待窗内至少复投 1 次
    assert mock_enqueue.await_count >= 2
    assert fallback_invoked is False

    cleanup_task(task_name)


@pytest.mark.asyncio
async def test_create_background_task_fallback_disabled_does_not_spin() -> None:
    """形态 A 兜底自旋防御: BACKGROUND_FALLBACK_ENABLED=false + create_background_task 直调,
    任务在 1s 内 done, 心跳 stage=failed, 无 fallback_waiting。
    """
    import asyncio as _asyncio

    from app.core.tasks import create_background_task

    task_name = "pytest_create_bg_fallback_off_case"
    cleanup_task(task_name)
    fallback_invoked = False

    async def _fallback_coro() -> None:
        nonlocal fallback_invoked
        fallback_invoked = True
        await _asyncio.sleep(0)

    with patch("app.core.config.settings.BACKGROUND_FALLBACK_ENABLED", False):
        task = create_background_task(
            _fallback_coro(),
            name=task_name,
            task_type="writing_material_process",
            project_id=9200,
            fallback_control=True,
        )
        try:
            await _asyncio.wait_for(task, timeout=1.0)
        except _asyncio.TimeoutError:
            task.cancel()
            pytest.fail("fallback 禁用时 create_background_task 应立即 done, 不得自旋")

    assert fallback_invoked is False, "fallback 禁用时不应执行协程"
    hb = get_task_heartbeat(task_name)
    assert hb is not None
    assert hb.get("stage") == "failed"
    assert "BACKGROUND_FALLBACK_ENABLED=false" in str(hb.get("progress", ""))
    from app.core.tasks import inspect_task_liveness

    liveness = inspect_task_liveness(task_name)
    assert liveness.state == "terminal"
    assert liveness.active is False

    cleanup_task(task_name)


def test_worker_unavailable_heartbeat_terminal_liveness() -> None:
    """终态消费证明 (A4 #6): worker_unavailable 心跳经 inspect_task_liveness 返回 terminal,
    下游 dead-task 判定链路以此为入口, 防止后续改坏 TASK_TERMINAL_STAGES 时静默破坏收敛。
    """
    task_name = "pytest_terminal_liveness_case"
    cleanup_task(task_name)
    from app.core.tasks import (
        TASK_TERMINAL_STAGES,
        inspect_task_liveness,
        update_heartbeat,
    )

    update_heartbeat(
        task_name,
        stage="failed",
        progress="Worker 不可用: reason=function_worker_missing",
        extra={"queue_mode": "worker_unavailable"},
    )

    liveness = inspect_task_liveness(task_name)
    assert liveness.state == "terminal"
    assert liveness.active is False
    assert liveness.stage == "failed"
    assert "failed" in TASK_TERMINAL_STAGES

    cleanup_task(task_name)


@pytest.mark.asyncio
async def test_dispatch_waits_for_worker_then_queues() -> None:
    """worker 短暂缺失时应先 waiting_worker, 恢复后入队, 不直接 fallback。"""
    task_name = "pytest_dispatch_waiting_worker_case"
    cleanup_task(task_name)
    fallback_invoked = False

    async def _fallback() -> None:
        nonlocal fallback_invoked
        fallback_invoked = True

    mock_enqueue = AsyncMock(
        side_effect=[
            ArqDispatchResult(
                queued=False,
                function_name="writing_material_process_job",
                reason="worker_missing",
                queue_name="arq:queue:process",
            ),
            ArqDispatchResult(
                queued=True,
                function_name="writing_material_process_job",
                job_id="arq-job-after-wait",
                reason="queued",
                queue_name="arq:queue:process",
            ),
        ]
    )
    with (
        patch("app.core.arq_runtime.enqueue_job_detailed", mock_enqueue),
        patch("asyncio.sleep", AsyncMock()),
        patch("app.core.config.settings.WORKER_WAIT_BEFORE_FALLBACK_SEC", 0.1),
    ):
        queue_mode, arq_job_id = await dispatch_background_job(
            task_name=task_name,
            arq_function="writing_material_process_job",
            fallback_factory=lambda: _fallback(),
            task_type="writing_material_process",
            project_id=9003,
            arq_args=(9003,),
        )

    assert queue_mode == "arq"
    assert arq_job_id == "arq-job-after-wait"
    assert fallback_invoked is False
    assert mock_enqueue.await_count == 2

    cleanup_task(task_name)


# ---------------------------------------------------------------------------
# collusion stall snapshot 契约
# ---------------------------------------------------------------------------


def test_collusion_stall_snapshot_read_write_local_fallback() -> None:
    """本地降级路径下, read/write/clear 行为与旧字典一致。
    不依赖 Redis; 若环境中有 Redis, 也不破坏本地语义。"""
    project_id = 999_001
    clear_collusion_cache(project_id)
    assert read_collusion_stall_snapshot(project_id) is None

    write_collusion_stall_snapshot(
        project_id, timestamp=1_700_000_000.5, compared_count=7,
    )
    snapshot = read_collusion_stall_snapshot(project_id)
    assert snapshot is not None
    assert snapshot["timestamp"] == pytest.approx(1_700_000_000.5)
    assert snapshot["compared_count"] == 7

    clear_collusion_cache(project_id)
    assert read_collusion_stall_snapshot(project_id) is None


def test_collusion_stall_snapshot_rewrite_updates_timestamp() -> None:
    """相同 project_id 再写入时, 新数据必须覆盖旧数据, 不可残留旧 compared_count。"""
    project_id = 999_002
    clear_collusion_cache(project_id)

    write_collusion_stall_snapshot(
        project_id, timestamp=100.0, compared_count=3,
    )
    write_collusion_stall_snapshot(
        project_id, timestamp=200.0, compared_count=5,
    )
    snapshot = read_collusion_stall_snapshot(project_id)
    assert snapshot is not None
    assert snapshot["compared_count"] == 5
    assert snapshot["timestamp"] == pytest.approx(200.0)

    clear_collusion_cache(project_id)


# ---------------------------------------------------------------------------
# writing material process 触发器契约
# ---------------------------------------------------------------------------


def test_writing_material_process_task_exposes_expected_api() -> None:
    """任务模块必须暴露统一的任务名工厂和运行入口;
    名称格式需与 dispatch_background_job 中使用的一致。"""
    from app.core.tasks.writing_material_process_task import (
        get_writing_material_process_task_name,
        run_writing_material_process_task,
    )

    assert get_writing_material_process_task_name(42) == "writing_material_process_42"
    assert asyncio.iscoroutinefunction(run_writing_material_process_task)


def test_writing_compose_single_task_exposes_expected_api() -> None:
    from app.core.tasks.writing_compose_single_task import (
        get_writing_compose_single_task_name,
        run_writing_compose_single_task,
    )

    assert (
        get_writing_compose_single_task_name(42, 7)
        == "writing_compose_single_42_7"
    )
    assert asyncio.iscoroutinefunction(run_writing_compose_single_task)


def test_writing_image_llm_jobs_are_llm_resource() -> None:
    """图片 VL / 分类子任务必须进入 LLM 资源队列, 不得占用 process worker。"""
    from app.core.tasks.document_image_llm_task import DOCUMENT_IMAGE_VL_JOB
    from app.core.tasks.resource_policy import resource_type_for_function
    from app.core.tasks.writing_image_llm_task import (
        WRITING_IMAGE_CLASSIFY_JOB,
        WRITING_IMAGE_VL_JOB,
    )

    assert resource_type_for_function(DOCUMENT_IMAGE_VL_JOB) == "llm"
    assert resource_type_for_function(WRITING_IMAGE_VL_JOB) == "llm"
    assert resource_type_for_function(WRITING_IMAGE_CLASSIFY_JOB) == "llm"


def test_writing_image_llm_task_exposes_expected_contract() -> None:
    """图片 LLM 子任务模块必须暴露稳定 job 名与结果 key。"""
    from app.core.tasks.document_image_llm_task import (
        DOCUMENT_IMAGE_VL_JOB,
    )
    from app.core.tasks.document_image_llm_task import (
        result_key as document_image_result_key,
    )
    from app.core.tasks.writing_image_llm_task import (
        WRITING_IMAGE_CLASSIFY_JOB,
        WRITING_IMAGE_VL_JOB,
        result_key,
    )

    assert DOCUMENT_IMAGE_VL_JOB == "document_image_vl_job"
    assert document_image_result_key("batch-1").endswith(":batch-1")
    assert WRITING_IMAGE_VL_JOB == "writing_image_vl_job"
    assert WRITING_IMAGE_CLASSIFY_JOB == "writing_image_classify_job"
    assert result_key("batch-1").endswith(":batch-1")
