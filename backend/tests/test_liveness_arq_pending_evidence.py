"""ARQ 队列侧活性证据的防御测试

背景：mark_task_queued 只在入队时写一次心跳，worker 领取前无人刷新。
正常排队的任务在心跳超时窗口内会被 inspect_task_liveness 误判为 stale，
进而将项目写成 FAILED。has_confirmed_pending_arq_job 在心跳过期时提供
队列侧证据，避免"排队等待"被误当"卡死"。

覆盖：
 A. has_confirmed_pending_arq_job 单元场景（11 个）
 B. inspect_task_liveness 集成场景（3 个）

patch 目标说明：
 - get_sync_redis 在 liveness_evidence.py 函数体内以
 ``from app.core.redis import get_sync_redis`` 懒导入，
 patch 目标为 ``app.core.redis.get_sync_redis``（模块级属性，
 懒导入在调用时从该属性取值，故 patch 生效）。
 - has_confirmed_pending_arq_job 在 inspect_task_liveness 函数体内
 以 ``from app.core.tasks.lifecycle.liveness_evidence import ...``
 懒导入，patch 目标为
 ``app.core.tasks.lifecycle.liveness_evidence.has_confirmed_pending_arq_job``。
 - get_task_heartbeat 在 inspect_task_liveness 和 is_task_in_enqueue_grace
 均通过模块命名空间解析，patch 目标为 ``app.core.tasks.get_task_heartbeat``。
"""

from __future__ import annotations

import time
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest

from app.core.tasks.lifecycle.liveness_evidence import (
    has_confirmed_pending_arq_job,
)

# ─────────────────────── 工具函数 ───────────────────────


def _arq_heartbeat(
    *,
    job_id: str = "job-abc-123",
    queue_mode: str = "arq",
    enqueued_at: Optional[float] = None,
    include_enqueued_at: bool = True,
) -> dict[str, Any]:
    """构造合法的 ARQ 入队心跳快照。

    enqueued_at 默认为 60 秒前（远未超限但明显不新鲜），
    可通过参数覆盖。include_enqueued_at=False 时完全省略该字段。
    """
    hb: dict[str, Any] = {
        "queue_mode": queue_mode,
        "arq_job_id": job_id,
        "last_heartbeat": time.time() - 200,
        "stage": "queued",
    }
    if include_enqueued_at:
        hb["enqueued_at"] = enqueued_at if enqueued_at is not None else (time.time() - 60)
    return hb


# ═══════════════════════════════════════════════════════════════════════
# A. has_confirmed_pending_arq_job 单元测试
# ═══════════════════════════════════════════════════════════════════════


class TestHasConfirmedPendingArqJob:
    """覆盖 11 个独立场景，不依赖任何外部服务。"""

    # 场景 1
    def test_none_heartbeat_returns_false(self) -> None:
        """heartbeat=None 直接返回 False，不尝试访问 Redis。"""
        result = has_confirmed_pending_arq_job(None)
        assert result is False, "心跳为 None 时必须返回 False"

    # 场景 2
    def test_non_arq_queue_mode_returns_false_without_redis(self) -> None:
        """queue_mode 不是 arq 时立即返回 False，且不应触发 Redis 查询。"""
        non_arq_modes = ("asyncio_fallback", "waiting_worker", "", "ARQ_UPPER")
        for mode in non_arq_modes:
            hb: dict[str, Any] = {"queue_mode": mode, "arq_job_id": "job-123"}
            with patch("app.core.redis.get_sync_redis") as mock_get_redis:
                result = has_confirmed_pending_arq_job(hb)
            assert result is False, f"queue_mode={mode!r} 时应返回 False"
            mock_get_redis.assert_not_called(), (
                f"queue_mode={mode!r} 时不得调用 get_sync_redis"
            )

    # 场景 3
    def test_missing_or_empty_arq_job_id_returns_false(self) -> None:
        """arq_job_id 缺失或为空串/纯空格时返回 False。"""
        for bad_id in (None, "", "   "):
            hb: dict[str, Any] = {"queue_mode": "arq", "arq_job_id": bad_id}
            with patch("app.core.redis.get_sync_redis"):
                result = has_confirmed_pending_arq_job(hb)
            assert result is False, f"arq_job_id={bad_id!r} 时应返回 False"

    # 场景 4
    def test_redis_exists_nonzero_returns_true(self) -> None:
        """所有必要字段齐备且 Redis exists 返回 1 时，应返回 True。"""
        hb = _arq_heartbeat()
        mock_redis = MagicMock()
        mock_redis.exists.return_value = 1
        with patch("app.core.redis.get_sync_redis", return_value=mock_redis):
            result = has_confirmed_pending_arq_job(hb)
        assert result is True, "Redis exists=1 时应返回 True"

    # 场景 5
    def test_redis_exists_zero_returns_false(self) -> None:
        """字段齐备但 Redis exists 返回 0（job 不在队列中）时返回 False。"""
        hb = _arq_heartbeat()
        mock_redis = MagicMock()
        mock_redis.exists.return_value = 0
        with patch("app.core.redis.get_sync_redis", return_value=mock_redis):
            result = has_confirmed_pending_arq_job(hb)
        assert result is False, "Redis exists=0 时应返回 False（job 不在队列）"

    # 场景 6
    def test_enqueued_at_too_old_returns_false_without_arq_function(self) -> None:
        """未传 arq_function 时保留入队滞留封顶（最后防呆）。

 后该封顶仅约束未知调用方；已传 arq_function 的路径以
 worker 存活为准（见场景 16/17）。
 """
        max_sec = 60.0
        old_ts = time.time() - max_sec - 15  # 明确超限
        hb = _arq_heartbeat(enqueued_at=old_ts)
        mock_redis = MagicMock()
        mock_redis.exists.return_value = 1
        with patch("app.core.redis.get_sync_redis", return_value=mock_redis):
            result = has_confirmed_pending_arq_job(hb, max_pending_sec=max_sec)
        assert result is False, "未传 arq_function 且入队超限时必须返回 False"

    # 场景 7
    def test_missing_enqueued_at_falls_back_to_redis(self) -> None:
        """enqueued_at 字段缺失时不因此拒绝，仍按 Redis 结果返回。"""
        hb = _arq_heartbeat(include_enqueued_at=False)
        mock_redis = MagicMock()
        mock_redis.exists.return_value = 1
        with patch("app.core.redis.get_sync_redis", return_value=mock_redis):
            result = has_confirmed_pending_arq_job(hb)
        assert result is True, "enqueued_at 缺失时应按 Redis 结果判定（Redis=1 → True）"

    # 场景 8
    def test_invalid_enqueued_at_treated_as_missing_no_exception(self) -> None:
        """enqueued_at 为非法字符串时不抛异常，效果等同字段缺失，仍按 Redis 判定。"""
        hb = _arq_heartbeat()
        hb["enqueued_at"] = "abc_not_a_float"
        mock_redis = MagicMock()
        mock_redis.exists.return_value = 1
        with patch("app.core.redis.get_sync_redis", return_value=mock_redis):
            result = has_confirmed_pending_arq_job(hb)
        assert result is True, "非法 enqueued_at 应按缺失处理，Redis=1 则返回 True"

    # 场景 9
    def test_get_sync_redis_returns_none_returns_false(self) -> None:
        """get_sync_redis() 返回 None（Redis 不可用）时返回 False。"""
        hb = _arq_heartbeat()
        with patch("app.core.redis.get_sync_redis", return_value=None):
            result = has_confirmed_pending_arq_job(hb)
        assert result is False, "Redis 客户端为 None 时必须返回 False"

    # 场景 10
    def test_redis_exists_raises_exception_returns_false(self) -> None:
        """Redis exists 调用抛异常时返回 False，不得返回 True。

        本函数用于"挽救误判"，与 arq_runtime.arq_job_present_async 方向相反：
        后者"无法证伪即视为在途"；本函数"只有明确证实才返回 True"，
        Redis 异常属于"无法证实"，必须保守地返回 False，避免任务永久停留在活跃态。
        """
        hb = _arq_heartbeat()
        mock_redis = MagicMock()
        mock_redis.exists.side_effect = ConnectionError("模拟 Redis 连接中断")
        with patch("app.core.redis.get_sync_redis", return_value=mock_redis):
            result = has_confirmed_pending_arq_job(hb)
        assert result is False, (
            "Redis 异常时必须返回 False（保守方向：无法证实 = 不挽救）"
        )

    # 场景 11
    def test_redis_exists_probes_only_pending_key_prefixes(self) -> None:
        """exists 的入参只覆盖 job/in-progress/retry 三类未终局前缀。: result key 表示 job 已终局（含异常终止），不得作为在途证据——
 崩溃 job 的 result key 曾把失活判定拖满 30 分钟。
 """
        from arq.constants import (
            in_progress_key_prefix,
            job_key_prefix,
            result_key_prefix,
            retry_key_prefix,
        )

        job_id = "scenario-11-unique-job-id"
        hb = _arq_heartbeat(job_id=job_id)
        mock_redis = MagicMock()
        mock_redis.exists.return_value = 1

        with patch("app.core.redis.get_sync_redis", return_value=mock_redis):
            has_confirmed_pending_arq_job(hb)

        mock_redis.exists.assert_called_once()
        actual_keys: set[str] = set(mock_redis.exists.call_args[0])
        expected_keys: set[str] = {
            f"{job_key_prefix}{job_id}",
            f"{in_progress_key_prefix}{job_id}",
            f"{retry_key_prefix}{job_id}",
        }
        assert actual_keys == expected_keys, (
            f"Redis exists 只应探测三类未终局 ARQ key 前缀。\n"
            f"期望: {expected_keys}\n"
            f"实际: {actual_keys}"
        )
        assert f"{result_key_prefix}{job_id}" not in actual_keys, (
            "result key 是终局证据，禁止进入在途探测集合"
        )

    # 场景 15 
    def test_result_key_only_returns_false(self) -> None:
        """仅 result key 存在（job 已终局）时返回 False，不再挽救。

        模拟真实 Redis: 对三类未终局键探测返回 0（result key 不在探测集合内）。
        """
        from arq.constants import result_key_prefix

        job_id = "scenario-15-finished-job"
        hb = _arq_heartbeat(job_id=job_id)
        existing_keys = {f"{result_key_prefix}{job_id}"}

        def _exists(*keys: str) -> int:
            return sum(1 for key in keys if key in existing_keys)

        mock_redis = MagicMock()
        mock_redis.exists.side_effect = _exists
        with patch("app.core.redis.get_sync_redis", return_value=mock_redis):
            result = has_confirmed_pending_arq_job(hb, arq_function="writing_compose_job")
        assert result is False, "已终局(仅 result key) 的 job 不得被挽救为在途"

    # 场景 16 
    def test_pending_with_alive_worker_ignores_wall_clock(self) -> None:
        """有未终局痕迹 + worker 存活 → True，入队时长不参与判定。

 三大流程存在合法超长任务，禁止用入队墙钟判死（用户约束）。
 """
        hb = _arq_heartbeat(enqueued_at=time.time() - 7200)  # 远超旧 1800s 封顶
        mock_redis = MagicMock()
        mock_redis.exists.return_value = 1
        with (
            patch("app.core.redis.get_sync_redis", return_value=mock_redis),
            patch("app.core.arq_runtime.has_alive_worker_for", return_value=True),
        ):
            result = has_confirmed_pending_arq_job(hb, arq_function="writing_compose_job")
        assert result is True, "worker 存活且有未终局痕迹时，入队时长不得判死"

    # 场景 17 
    def test_pending_with_transient_worker_gap_still_rescues(self) -> None:
        """有未终局痕迹 + worker 键瞬时缺失未确认 → 仍挽救。"""
        hb = _arq_heartbeat()
        mock_redis = MagicMock()
        mock_redis.exists.return_value = 1
        with (
            patch("app.core.redis.get_sync_redis", return_value=mock_redis),
            patch("app.core.arq_runtime.has_alive_worker_for", return_value=False),
            patch(
                "app.core.tasks.lifecycle.is_worker_confirmed_missing",
                return_value=False,
            ),
        ):
            result = has_confirmed_pending_arq_job(hb, arq_function="writing_compose_job")
        assert result is True, "worker 瞬时断档未确认时必须按在途挽救"

    # 场景 17b 
    def test_pending_with_confirmed_missing_worker_still_rescues_within_window(self) -> None:
        """有未终局痕迹 + worker 缺失已确认 + 心跳未越无进展窗 → 仍挽救。

        存活键过期不等于执行体消失。夹具实体为 writing_compose_job。
        """
        hb = _arq_heartbeat()
        mock_redis = MagicMock()
        mock_redis.exists.return_value = 1
        with (
            patch("app.core.redis.get_sync_redis", return_value=mock_redis),
            patch("app.core.arq_runtime.has_alive_worker_for", return_value=False),
            patch(
                "app.core.tasks.lifecycle.is_worker_confirmed_missing",
                return_value=True,
            ),
        ):
            result = has_confirmed_pending_arq_job(hb, arq_function="writing_compose_job")
        assert result is True, "worker 键过期但心跳未越无进展窗时必须按在途挽救"

    # 场景 17c ：同形态第二例审查 + 本案例写标
    @pytest.mark.parametrize(
        "arq_function",
        ["review_project_job", "writing_compose_job"],
    )
    def test_pending_with_confirmed_missing_and_no_progress_window_is_zombie(
        self, arq_function: str
    ) -> None:
        """痕迹仍在但 worker 缺失已确认且心跳越过无进展窗 → 按僵尸不挽救。"""
        hb = _arq_heartbeat()
        hb["last_heartbeat"] = time.time() - 1900
        hb["enqueued_at"] = time.time() - 2000
        mock_redis = MagicMock()
        mock_redis.exists.return_value = 1
        with (
            patch("app.core.redis.get_sync_redis", return_value=mock_redis),
            patch("app.core.arq_runtime.has_alive_worker_for", return_value=False),
            patch(
                "app.core.tasks.lifecycle.is_worker_confirmed_missing",
                return_value=True,
            ),
        ):
            result = has_confirmed_pending_arq_job(hb, arq_function=arq_function)
        assert result is False, "越过无进展窗的僵尸痕迹不得挽救"

    # 场景 18 
    def test_worker_probe_exception_still_rescues_when_job_pending(self) -> None:
        """worker 探测抛异常时队列痕迹仍在 → 挽救（无法观测 ≠ 死亡）。"""
        hb = _arq_heartbeat()
        mock_redis = MagicMock()
        mock_redis.exists.return_value = 1
        with (
            patch("app.core.redis.get_sync_redis", return_value=mock_redis),
            patch(
                "app.core.arq_runtime.has_alive_worker_for",
                side_effect=ConnectionError("模拟 worker 心跳读取失败"),
            ),
        ):
            result = has_confirmed_pending_arq_job(hb, arq_function="writing_compose_job")
        assert result is True, "worker 探测异常且队列痕迹在时必须挽救"


# ═══════════════════════════════════════════════════════════════════════
# B. inspect_task_liveness 集成测试
# ═══════════════════════════════════════════════════════════════════════


class TestInspectTaskLivenessArqPendingIntegration:
    """验证 inspect_task_liveness 在 修复路径上的行为。

 所有测试均通过 patch get_task_heartbeat 注入心跳，
 无需真实 Redis 或 asyncio 任务。
 任务名称不注册到 _background_tasks，确保走纯心跳判定路径。
 """

    def _stale_arq_heartbeat(self, job_id: str = "job-stale-001") -> dict[str, Any]:
        """构造心跳年龄超过 HEARTBEAT_TIMEOUT 的 ARQ 入队心跳。

        enqueued_at 设为 300 秒前，大于 ENQUEUE_GRACE_SEC=120，
        确保 is_task_in_enqueue_grace 返回 False（不走宽限路径）。
        """
        from app.core.tasks import HEARTBEAT_TIMEOUT

        return {
            "queue_mode": "arq",
            "arq_job_id": job_id,
            "last_heartbeat": time.time() - HEARTBEAT_TIMEOUT - 30,
            "stage": "queued",
            "enqueued_at": time.time() - 300,
        }

    # 场景 12
    def test_stale_heartbeat_confirmed_job_returns_live(self) -> None:
        """心跳过期 + has_confirmed_pending_arq_job=True → state=live, reason=arq_job_pending。"""
        heartbeat = self._stale_arq_heartbeat()

        with (
            patch("app.core.tasks.get_task_heartbeat", return_value=heartbeat),
            patch(
                "app.core.tasks.lifecycle.liveness_evidence.has_confirmed_pending_arq_job",
                return_value=True,
            ),
        ):
            from app.core.tasks import inspect_task_liveness

            result = inspect_task_liveness("_test_stale_confirmed_live_task")

        assert result.state == "live", (
            f"job 确证在途时 state 应为 'live'，实际: {result.state!r}"
        )
        assert result.active is True, "job 确证在途时 active 应为 True"
        assert result.reason == "arq_job_pending", (
            f"reason 应为 'arq_job_pending'，实际: {result.reason!r}"
        )
        assert result.is_live is True, "is_live 属性应为 True"

    # 场景 13
    def test_missing_last_heartbeat_without_arq_evidence_is_stale(self) -> None:
        """心跳有 stage 但没有 last_heartbeat 时不能证明新鲜，应按 stale 而不是 live。"""
        heartbeat = {"stage": "mandatory_coverage", "progress": "52%|核验"}

        with (
            patch("app.core.tasks.get_task_heartbeat", return_value=heartbeat),
            patch(
                "app.core.tasks.lifecycle.liveness_evidence.has_confirmed_pending_arq_job",
                return_value=False,
            ),
        ):
            from app.core.tasks import inspect_task_liveness

            result = inspect_task_liveness("_test_missing_timestamp")

        assert result.state == "stale"
        assert result.reason == "heartbeat_timestamp_missing"
        assert result.is_live is False
        """心跳过期 + has_confirmed_pending_arq_job=False → state=stale, reason=heartbeat_stale。

        证明 修复未破坏原有失活判定行为：job 未确证时应维持 stale。
        """
        heartbeat = self._stale_arq_heartbeat("job-stale-002")

        with (
            patch("app.core.tasks.get_task_heartbeat", return_value=heartbeat),
            patch(
                "app.core.tasks.lifecycle.liveness_evidence.has_confirmed_pending_arq_job",
                return_value=False,
            ),
        ):
            from app.core.tasks import inspect_task_liveness

            result = inspect_task_liveness("_test_stale_unconfirmed_stale_task")

        assert result.state == "stale", (
            f"job 未确证时 state 应为 'stale'，实际: {result.state!r}"
        )
        assert result.reason == "heartbeat_stale", (
            f"reason 应为 'heartbeat_stale'，实际: {result.reason!r}"
        )
        assert result.is_live is False, "is_live 属性应为 False"

    # 场景 14
    def test_fresh_heartbeat_does_not_call_has_confirmed_pending_arq_job(self) -> None:
        """心跳新鲜（age < timeout）时不得调用 has_confirmed_pending_arq_job。

        has_confirmed_pending_arq_job 仅在心跳超时分支触发；
        每次状态查询都打 Redis 会带来无谓开销，此测试锁定该防线。
        """
        fresh_heartbeat: dict[str, Any] = {
            "queue_mode": "arq",
            "arq_job_id": "job-fresh-999",
            "last_heartbeat": time.time() - 5,  # 远未超时
            "stage": "running",  # 非 queued，避免走 enqueue_grace 短路径
        }

        with (
            patch("app.core.tasks.get_task_heartbeat", return_value=fresh_heartbeat),
            patch(
                "app.core.tasks.lifecycle.liveness_evidence.has_confirmed_pending_arq_job",
            ) as mock_has_confirmed,
        ):
            from app.core.tasks import inspect_task_liveness

            result = inspect_task_liveness("_test_fresh_heartbeat_no_redis_task")

        mock_has_confirmed.assert_not_called(), (
            "心跳新鲜时不得调用 has_confirmed_pending_arq_job"
        )
        assert result.is_live is True, (
            f"新鲜心跳下任务应判为 live，实际 state={result.state!r}"
        )

    # 场景 19 
    def test_inspect_passes_arq_function_to_evidence_probe(self) -> None:
        """inspect_task_liveness 必须把 arq_function 透传给在途证据判定。

        缺少透传时证据判定只能退回入队墙钟防呆，worker 存活证据失效。
        """
        heartbeat = self._stale_arq_heartbeat("job-passthrough-001")

        with (
            patch("app.core.tasks.get_task_heartbeat", return_value=heartbeat),
            patch(
                "app.core.tasks.lifecycle.liveness_evidence.has_confirmed_pending_arq_job",
                return_value=True,
            ) as mock_has_confirmed,
        ):
            from app.core.tasks import inspect_task_liveness

            inspect_task_liveness(
                "_test_arq_function_passthrough_task",
                arq_function="writing_compose_job",
            )

        mock_has_confirmed.assert_called_once()
        assert (
            mock_has_confirmed.call_args.kwargs.get("arq_function")
            == "writing_compose_job"
        ), "inspect_task_liveness 必须以 arq_function 关键字透传函数名"
