"""R2-4 Step 2: 心跳契约字节级快照防御测试

锚定 ``MaterialBatchHeartbeat.emit`` 与 ``MaterialService.get_batch_process_status``
心跳字段输出的 byte-equal 行为，防止后续拆分阶段误改 phase 字符串集合、progress
格式、_FRONTEND_PHASE_ALIAS 映射、_TERMINAL_HB_PHASES 集合。

设计动机（R2-4 Plan §6.1）：
- 卡片明文要求"心跳字段语义零变化"；
- 真实约束源是前端 WritingMaterials 页面通过 ``get_batch_process_status`` 消费的
  ``phase`` / ``vl_progress`` / ``heartbeat_phase`` 字段；
- 历史 _set_phase 闭包字面行为：``progress = f"{pct}%|{message}" if message else f"{pct}%|"``。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.services.writing.material_batch.heartbeat import MaterialBatchHeartbeat

# ---------------------------------------------------------------------------
# 1. MaterialBatchHeartbeat 字节级契约
# ---------------------------------------------------------------------------


class TestHeartbeatPhaseSetByteEqual:
    """phase 字符串集合不得漂移：与历史 _set_phase 闭包调用点一致。"""

    EXPECTED_PHASES = {
        "parsing",
        "vl_recognition",
        "indexing",
        "image_classify",
        "label_resolve",
        "tagging",
        "completed",
    }

    def test_class_constants_match_expected_set(self) -> None:
        actual = {
            MaterialBatchHeartbeat.PHASE_PARSING,
            MaterialBatchHeartbeat.PHASE_VL_RECOGNITION,
            MaterialBatchHeartbeat.PHASE_INDEXING,
            MaterialBatchHeartbeat.PHASE_IMAGE_CLASSIFY,
            MaterialBatchHeartbeat.PHASE_LABEL_RESOLVE,
            MaterialBatchHeartbeat.PHASE_TAGGING,
            MaterialBatchHeartbeat.PHASE_COMPLETED,
        }
        assert actual == self.EXPECTED_PHASES

    def test_phase_string_literals_byte_equal(self) -> None:
        assert MaterialBatchHeartbeat.PHASE_PARSING == "parsing"
        assert MaterialBatchHeartbeat.PHASE_VL_RECOGNITION == "vl_recognition"
        assert MaterialBatchHeartbeat.PHASE_INDEXING == "indexing"
        assert MaterialBatchHeartbeat.PHASE_IMAGE_CLASSIFY == "image_classify"
        assert MaterialBatchHeartbeat.PHASE_LABEL_RESOLVE == "label_resolve"
        assert MaterialBatchHeartbeat.PHASE_TAGGING == "tagging"
        assert MaterialBatchHeartbeat.PHASE_COMPLETED == "completed"


class TestHeartbeatEmitArgsByteEqual:
    """emit() 调用 update_heartbeat 时的参数必须与原 _set_phase 闭包 byte-equal。"""

    def _patch_update_heartbeat(self, monkeypatch: pytest.MonkeyPatch) -> Mock:
        captured = Mock()
        monkeypatch.setattr(
            "app.core.tasks.update_heartbeat",
            captured,
        )
        return captured

    def test_emit_with_message_byte_equal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured = self._patch_update_heartbeat(monkeypatch)
        hb = MaterialBatchHeartbeat(task_name="writing_material_process_42")

        hb.emit("parsing", progress_pct=5, message="解析文档 0/3")

        captured.assert_called_once_with(
            "writing_material_process_42",
            stage="parsing",
            progress="5%|解析文档 0/3",
            extra={"phase": "parsing"},
        )

    def test_emit_without_message_uses_pipe_suffix_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = self._patch_update_heartbeat(monkeypatch)
        hb = MaterialBatchHeartbeat(task_name="writing_material_process_42")

        hb.emit("indexing", progress_pct=62)

        captured.assert_called_once_with(
            "writing_material_process_42",
            stage="indexing",
            progress="62%|",
            extra={"phase": "indexing"},
        )

    def test_emit_with_vl_progress_attaches_extra_field(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = self._patch_update_heartbeat(monkeypatch)
        hb = MaterialBatchHeartbeat(task_name="writing_material_process_42")

        hb.emit(
            "vl_recognition",
            progress_pct=37,
            message="VL识别 3/10",
            vl_done=3,
            vl_total=10,
        )

        captured.assert_called_once_with(
            "writing_material_process_42",
            stage="vl_recognition",
            progress="37%|VL识别 3/10",
            extra={"phase": "vl_recognition", "vl_progress": "3/10"},
        )

    def test_emit_with_only_vl_done_does_not_attach_vl_progress(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """与原 _set_phase 闭包契约一致：``vl_done`` 与 ``vl_total`` 必须同时不为 None。"""
        captured = self._patch_update_heartbeat(monkeypatch)
        hb = MaterialBatchHeartbeat(task_name="writing_material_process_42")

        hb.emit("vl_recognition", progress_pct=25, vl_done=1)
        hb.emit("vl_recognition", progress_pct=25, vl_total=10)

        for call in captured.call_args_list:
            assert call.kwargs["extra"] == {"phase": "vl_recognition"}

    def test_emit_with_empty_task_name_is_noop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = self._patch_update_heartbeat(monkeypatch)
        hb = MaterialBatchHeartbeat(task_name=None)

        hb.emit("parsing", progress_pct=5, message="x")

        captured.assert_not_called()

        hb_empty = MaterialBatchHeartbeat(task_name="")
        hb_empty.emit("parsing", progress_pct=5, message="x")
        captured.assert_not_called()


# ---------------------------------------------------------------------------
# 2. get_batch_process_status 前端契约：phase alias + terminal phases
# ---------------------------------------------------------------------------


class TestGetBatchProcessStatusPhaseAliasByteEqual:
    """``_FRONTEND_PHASE_ALIAS`` / ``_TERMINAL_HB_PHASES`` 决定前端展示分支。

    历史卡片明文：``label_resolve`` → ``tagging``、``starting`` / ``queued`` → ``parsing``；
    terminal: ``{completed, done, failed, cancelled}``。
    """

    @pytest.mark.asyncio
    async def test_label_resolve_aliased_to_tagging(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = await self._invoke_get_batch_status(monkeypatch, hb_stage="label_resolve")
        assert result["phase"] == "tagging"
        assert result["heartbeat_phase"] == "label_resolve"

    @pytest.mark.asyncio
    async def test_starting_aliased_to_parsing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = await self._invoke_get_batch_status(monkeypatch, hb_stage="starting")
        assert result["phase"] == "parsing"
        assert result["heartbeat_phase"] == "starting"

    @pytest.mark.asyncio
    async def test_queued_aliased_to_parsing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = await self._invoke_get_batch_status(monkeypatch, hb_stage="queued")
        assert result["phase"] == "parsing"

    @pytest.mark.asyncio
    async def test_terminal_completed_returns_done(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = await self._invoke_get_batch_status(
            monkeypatch,
            hb_stage="completed",
            task_live=False,
            is_stale=True,
            is_missing=False,
        )
        assert result["phase"] == "done"

    @pytest.mark.asyncio
    async def test_terminal_failed_byte_equal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = await self._invoke_get_batch_status(
            monkeypatch,
            hb_stage="failed",
            task_live=False,
            is_stale=True,
            is_missing=False,
        )
        assert result["phase"] == "failed"

    @pytest.mark.asyncio
    async def test_unknown_non_terminal_phase_passthrough(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """非 alias 也非 terminal 的非空 phase 在活跃心跳下原样返回。"""
        result = await self._invoke_get_batch_status(
            monkeypatch,
            hb_stage="indexing",
            task_live=True,
        )
        assert result["phase"] == "indexing"

    async def _invoke_get_batch_status(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        hb_stage: str,
        task_live: bool = True,
        is_stale: bool = False,
        is_missing: bool = False,
    ) -> dict:
        from app.services.writing.material_service import MaterialService

        db = SimpleNamespace(
            execute=AsyncMock(
                return_value=SimpleNamespace(
                    scalars=lambda: SimpleNamespace(all=lambda: [])
                )
            )
        )
        resolver = AsyncMock(return_value=SimpleNamespace(id=1))
        refresh_hook = AsyncMock()
        service = MaterialService(
            db=db,
            project_resolver=resolver,
            refresh_material_title_derivatives_hook=refresh_hook,
        )

        monkeypatch.setattr(
            "app.core.tasks.inspect_task_liveness",
            lambda *_a, **_kw: SimpleNamespace(
                is_live=task_live,
                is_stale=is_stale,
                is_missing=is_missing,
                state="active" if task_live else ("stale" if is_stale else "missing"),
                heartbeat={"phase": hb_stage, "stage": hb_stage, "vl_progress": ""},
            ),
        )
        monkeypatch.setattr(
            "app.services.retry_service.RetryService.get_writing_material_retry_state",
            AsyncMock(
                return_value={
                    "retryable": False,
                    "failed_node": None,
                    "target_id": None,
                    "failed_stage": None,
                    "retry_action_label": None,
                    "preserved_outputs": [],
                    "reset_scope": [],
                    "last_attempt": 0,
                    "retry_running": False,
                    "last_error": None,
                }
            ),
        )

        return await service.get_batch_process_status(project_id=1, user=SimpleNamespace())
