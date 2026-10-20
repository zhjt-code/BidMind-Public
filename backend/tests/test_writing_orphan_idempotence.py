""" 写标孤儿任务幂等机制防御测试。

修复背景:
 _resolve_post_extract_payload 的孤儿分支（liveness.state in {"stale","missing"}）
 原先未持久化 project.status，导致 DB 中项目状态仍为 OUTLINING/WRITING，
 前端 1Hz 轮询与 maintenance sweep 每次重新进入孤儿分支，产生重复告警与重复副作用
 （生产累计约 2965 条同指纹记录）。修复后写入 FAILED 终态，task_name_map 不含
 FAILED 状态，后续查询直接走 FAILED 短路分支，孤儿分支副作用只发生一次。

覆盖场景:
 O-1: 首次查询落终态（OUTLINING + stale -> FAILED 持久化 + db.commit/refresh）
 O-2: 第二次查询幂等（inspect_task_liveness 未再次调用，孤儿日志出现 1 次）
 O-3: 响应一致性（修复后 status 字段与 overall_status 同为 "failed"）
 O-4: MATCHING / WRITING 状态参数化（node_key 映射正确）
 O-5: missing 状态与 stale 同样处理
 O-6: 上传期边界不受影响（_resolve_upload_phase_payload 有意不写 FAILED 终态，
 静态断言以锁定该设计意图）
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.core.tasks import TaskLiveness
from app.models.project import ProjectStatus
from app.services.writing_service import WritingService


# ============================================================
# 通用桩 / 工厂（风格参照 test_writing_status_state_machine.py）
# ============================================================


def _project(
    status: ProjectStatus = ProjectStatus.OUTLINING,
    *,
    project_id: int = 1,
    name: str = "写标项目",
    error_message: str | None = None,
) -> SimpleNamespace:
    """构造轻量级 Project 替身，支持属性直接赋值（用于断言状态变更）。"""
    return SimpleNamespace(
        id=project_id,
        name=name,
        status=status,
        error_message=error_message,
    )


def _liveness(
    *,
    state: str = "stale",
    heartbeat: dict | None = None,
    stage: str = "generating",
    age_seconds: float | None = 300.0,
    reason: str = "heartbeat_stale",
) -> TaskLiveness:
    """构造任务活性检测结果替身。"""
    return TaskLiveness(
        task_name="test_orphan_task",
        state=state,
        active=(state == "live"),
        heartbeat=heartbeat,
        stage=stage,
        age_seconds=age_seconds,
        reason=reason,
    )


def _build_service(
    project: SimpleNamespace,
    tender: SimpleNamespace | None = None,
) -> WritingService:
    """构造依赖全部 Mock 化的 WritingService 实例。

    db.commit / db.refresh 均为 AsyncMock，允许在测试中断言 await 调用次数。
    _get_project_or_raise 始终返回同一 project 对象，保证幂等场景中两次调用
    拿到同一个内存对象（模拟 expire_on_commit=False 的 ORM 行为）。
    """
    db = SimpleNamespace(
        expire_all=Mock(),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    service = WritingService(db)  # type: ignore[arg-type]
    service._get_project_or_raise = AsyncMock(return_value=project)  # type: ignore[method-assign]
    service._get_tender_doc = AsyncMock(return_value=tender)  # type: ignore[method-assign]
    service._get_tender_doc_parsed = AsyncMock(  # type: ignore[method-assign]
        return_value=(tender.parsed if tender else None)
    )
    return service


# ============================================================
# O-1: 首次查询落终态
# ============================================================


@pytest.mark.asyncio
async def test_o1_orphan_outlining_stale_persists_failed_status() -> None:
    """孤儿分支首次触发时，project.status 必须持久化为 FAILED。

    验证要点:
    - project.status 被赋值为 FAILED
    - project.error_message 含"请从失败节点继续重试"
    - db.commit 与 db.refresh(project) 均被 await
    - mark_task_failed 被调用且 node_key == "writing_outline"
    - 返回视图 overall_status == "failed"
    """
    project = _project(ProjectStatus.OUTLINING)
    service = _build_service(project)
    stale = _liveness(state="stale", reason="heartbeat_stale")

    with (
        patch("app.services.writing_service.inspect_task_liveness", return_value=stale),
        patch(
            "app.services.retry_service.RetryService.mark_task_failed",
            new=AsyncMock(),
        ) as mark_failed,
        patch("app.core.ai.release_project_binding", new=AsyncMock()),
    ):
        result = await service.get_project_status(project_id=1, user=SimpleNamespace())

    assert project.status == ProjectStatus.FAILED, (
        f"project.status 应为 FAILED，实际为 {project.status.value}"
    )
    assert project.error_message is not None, (
        "孤儿分支必须写入 project.error_message，当前仍为 None"
    )
    assert "请从失败节点继续重试" in project.error_message, (
        f"error_message 应含 '请从失败节点继续重试'，实际内容: {project.error_message}"
    )
    service.db.commit.assert_awaited_once_with()
    service.db.refresh.assert_awaited_once_with(project)
    mark_failed.assert_awaited_once()
    actual_node_key = mark_failed.call_args.kwargs.get("node_key")
    assert actual_node_key == "writing_outline", (
        f"node_key 应为 writing_outline，实际: {actual_node_key}"
    )
    assert result["overall_status"] == "failed", (
        f"首次查询 overall_status 应为 failed，实际: {result['overall_status']}"
    )


# ============================================================
# O-2: 第二次查询幂等（本测试的核心）
# ============================================================


@pytest.mark.asyncio
async def test_o2_second_query_is_idempotent_skips_liveness_and_orphan_branch() -> None:
    """ 核心：第二次查询应走 FAILED 短路，孤儿分支副作用只发生一次。

 幂等机制原理：
 task_name_map 仅映射 EXTRACTING/OUTLINING/MATCHING/WRITING 四个状态，
 FAILED 不在其中，因此 task_name 为 None，inspect_task_liveness 不被调用，
 孤儿分支不被重入。

 验证要点（断言数量 = 幂等保证强度）:
 - inspect_task_liveness 全程仅被调用 1 次（首次查询）
 - "孤儿任务检测" WARNING 日志全程出现 1 次
 - mark_task_failed 全程仅被调用 1 次
 - 第二次查询返回 overall_status == "failed"，不因短路而回退成"进行中"
 """
    project = _project(ProjectStatus.OUTLINING)
    service = _build_service(project)
    stale = _liveness(state="stale", reason="heartbeat_stale")
    inspect_mock = Mock(return_value=stale)

    # 用 Mock 替换模块级 logger，捕获所有 warning 调用以计数"孤儿任务检测"日志
    warning_messages: list[str] = []

    def _capture_warning(*args: object, **_kwargs: object) -> None:
        # loguru 用 f-string 直接传格式化好的字符串，args[0] 即消息体
        if args:
            warning_messages.append(str(args[0]))

    with (
        patch("app.services.writing_service.inspect_task_liveness", inspect_mock),
        patch(
            "app.services.retry_service.RetryService.mark_task_failed",
            new=AsyncMock(),
        ) as mark_failed,
        patch("app.core.ai.release_project_binding", new=AsyncMock()),
        patch("app.services.writing_service.logger") as mock_logger,
    ):
        mock_logger.warning.side_effect = _capture_warning

        # 第一次查询：触发孤儿分支，project.status -> FAILED
        await service.get_project_status(project_id=1, user=SimpleNamespace())

        # 第二次查询：project 已是 FAILED，应走短路分支
        result2 = await service.get_project_status(project_id=1, user=SimpleNamespace())

    # inspect_task_liveness 只在首次查询的孤儿分支中被调用一次
    assert inspect_mock.call_count == 1, (
        f"inspect_task_liveness 应仅被调用 1 次（首次查询），"
        f"实际被调用 {inspect_mock.call_count} 次——说明第二次查询重入了孤儿分支"
    )

    # "孤儿任务检测" 日志只出现在首次查询中
    orphan_log_count = sum(1 for msg in warning_messages if "孤儿任务检测" in msg)
    assert orphan_log_count == 1, (
        f"孤儿任务检测日志应出现 1 次，实际出现 {orphan_log_count} 次"
        f"（捕获的全部 warning 消息: {warning_messages}）"
    )

    # mark_task_failed 不在第二次查询中重复调用
    assert mark_failed.call_count == 1, (
        f"mark_task_failed 应仅被调用 1 次，实际 {mark_failed.call_count} 次"
    )

    # 第二次查询仍正确表达失败状态，不因短路而错误显示"进行中"
    assert result2["overall_status"] == "failed", (
        f"第二次查询 overall_status 应为 failed，实际: {result2['overall_status']}"
    )


# ============================================================
# O-3: 响应一致性（status 字段与 overall_status 同为 "failed"）
# ============================================================


@pytest.mark.asyncio
async def test_o3_status_field_matches_overall_status_after_orphan_branch() -> None:
    """修复后 status 字段应与 overall_status 一致，均为 "failed"。

    修复前：project.status 未持久化，返回值 status=="outlining" 但
    overall_status=="failed"，前端只判 status==='failed' 因此无限轮询。
    修复后：project.status 被赋为 FAILED，status=="failed"，轮询得以终止。
    """
    project = _project(ProjectStatus.OUTLINING)
    service = _build_service(project)
    stale = _liveness(state="stale", reason="heartbeat_stale")

    with (
        patch("app.services.writing_service.inspect_task_liveness", return_value=stale),
        patch(
            "app.services.retry_service.RetryService.mark_task_failed",
            new=AsyncMock(),
        ),
        patch("app.core.ai.release_project_binding", new=AsyncMock()),
    ):
        result = await service.get_project_status(project_id=1, user=SimpleNamespace())

    # 两个字段必须一致，前端依赖 status 字段轮询终止条件
    assert result["status"] == "failed", (
        f"status 字段应为 failed（修复后 project.status 被赋值为 FAILED），"
        f"实际: {result['status']}。若为 outlining 则说明持久化未生效"
    )
    assert result["overall_status"] == "failed", (
        f"overall_status 应为 failed，实际: {result['overall_status']}"
    )
    assert result["status"] == result["overall_status"], (
        f"status({result['status']}) 与 overall_status({result['overall_status']}) "
        "不一致，前端轮询终止逻辑将出现歧义"
    )


# ============================================================
# O-4: MATCHING / WRITING 状态参数化
# ============================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "project_status, expected_node_key",
    [
        (ProjectStatus.MATCHING, "writing_match"),
        (ProjectStatus.WRITING, "writing_compose"),
    ],
)
async def test_o4_parametrized_status_writes_failed_with_correct_node_key(
    project_status: ProjectStatus,
    expected_node_key: str,
) -> None:
    """MATCHING / WRITING 孤儿分支均落 FAILED，且 node_key 与状态一一对应。"""
    project = _project(project_status)
    service = _build_service(project)
    stale = _liveness(state="stale", reason="heartbeat_stale")

    with (
        patch("app.services.writing_service.inspect_task_liveness", return_value=stale),
        patch(
            "app.services.retry_service.RetryService.mark_task_failed",
            new=AsyncMock(),
        ) as mark_failed,
        patch("app.core.ai.release_project_binding", new=AsyncMock()),
    ):
        result = await service.get_project_status(project_id=1, user=SimpleNamespace())

    assert project.status == ProjectStatus.FAILED, (
        f"project_status={project_status.value} 孤儿分支后应为 FAILED，"
        f"实际: {project.status.value}"
    )
    mark_failed.assert_awaited_once()
    actual_node_key = mark_failed.call_args.kwargs.get("node_key")
    assert actual_node_key == expected_node_key, (
        f"project_status={project_status.value} 时 node_key 应为 {expected_node_key}，"
        f"实际: {actual_node_key}"
    )
    assert result["overall_status"] == "failed", (
        f"overall_status 应为 failed，实际: {result['overall_status']}"
    )


# ============================================================
# O-5: missing 状态与 stale 同样处理
# ============================================================


@pytest.mark.asyncio
async def test_o5_missing_state_also_triggers_orphan_branch_and_persists_failed() -> None:
    """liveness.state == "missing" 与 "stale" 均落 FAILED 终态，不可遗漏。"""
    project = _project(ProjectStatus.OUTLINING)
    service = _build_service(project)
    missing = _liveness(state="missing", reason="task_not_found")

    with (
        patch("app.services.writing_service.inspect_task_liveness", return_value=missing),
        patch(
            "app.services.retry_service.RetryService.mark_task_failed",
            new=AsyncMock(),
        ) as mark_failed,
        patch("app.core.ai.release_project_binding", new=AsyncMock()),
    ):
        result = await service.get_project_status(project_id=1, user=SimpleNamespace())

    assert project.status == ProjectStatus.FAILED, (
        f"liveness.state='missing' 时 project.status 应为 FAILED，"
        f"实际: {project.status.value}"
    )
    assert project.error_message is not None, (
        "liveness.state='missing' 时 project.error_message 不应为 None"
    )
    service.db.commit.assert_awaited_once_with()
    mark_failed.assert_awaited_once()
    assert result["overall_status"] == "failed", (
        f"overall_status 应为 failed，实际: {result['overall_status']}"
    )


# ============================================================
# O-6: 上传期边界不受影响（行为断言）
# ============================================================


@pytest.mark.asyncio
async def test_o6_extracting_stale_liveness_does_not_write_failed_status() -> None:
    """EXTRACTING + liveness.stale 时，project.status 不得被写为 FAILED。

    _resolve_upload_phase_payload 中的约束提取失活（stale/missing）分支有意不写
    Project 终态：约束提取失败应保留 EXTRACTING，让用户在前端直接点击重试，
    而无需经历更复杂的 FAILED -> 重新触发恢复路径。这与 _resolve_post_extract_payload
    的孤儿分支行为形成有意对比——两段逻辑的差异是设计决策，不应被无意对齐。

    test_writing_status_recovery.py 中已有同等行为断言，此处再次锁定以强化边界。
    """
    project = _project(ProjectStatus.EXTRACTING)
    tender = SimpleNamespace(id=10, parsed=1, vl_skipped=None)
    service = _build_service(project, tender=tender)
    # 约束提取任务失活，模拟心跳超时
    stale_extract = _liveness(state="stale", reason="heartbeat_stale", stage="extracting")

    with (
        patch("app.services.writing_service.inspect_task_liveness", return_value=stale_extract),
        patch("app.core.tasks.get_task_heartbeat", return_value=None),
        patch("app.services.writing_service.is_task_active", return_value=False),
        patch("app.core.tasks.get_recovery_count", return_value=0),
        patch(
            "app.services.retry_service.RetryService.mark_task_failed",
            new=AsyncMock(),
        ) as mark_failed,
    ):
        result = await service.get_project_status(project_id=1, user=SimpleNamespace())

    # 核心边界：EXTRACTING 孤儿不得持久化 FAILED 终态
    assert project.status == ProjectStatus.EXTRACTING, (
        f"EXTRACTING + stale 时 project.status 应保持 EXTRACTING，"
        f"实际变为 {project.status.value}——破坏了让用户直接重试的设计意图"
    )
    # 视图层面仍正确表达失败（让前端提示重试，但不锁死状态机）
    assert result["overall_status"] == "failed", (
        f"返回视图 overall_status 应为 failed，实际: {result['overall_status']}"
    )
    # db.commit 不被调用（无 DB 写副作用）
    service.db.commit.assert_not_awaited()
    # mark_task_failed 仍被调用（更新 TaskRun 状态记录）
    mark_failed.assert_awaited_once()
