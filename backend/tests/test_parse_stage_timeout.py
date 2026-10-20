"""PDF解析超时熔断 + 阶段观测落库 防御测试

映射真实风险:
- 大文件/病态PDF解析在子进程内同步执行且无页级进度回传, 心跳保活又让看门狗
 永不恢复(副作用), 一旦解析超慢/卡死, 任务会永久停在“处理中”不报错。
 => 验证 run_parse_stage 的硬超时熔断: 超时必须终止子进程并抛出可读错误,
 使上层 handle_failure 能把任务判失败、在项目时间线显示红色终态。
- 进行中的任务在时间线缺少阶段级可见性。
 => 验证 record_stage 落一条 phase=STAGE 观测, 且 task_id 与 START/END 同链。
"""

from __future__ import annotations

import asyncio
import os

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test"
)

from app.core.tasks.process import parse_stage as parse_stage_module  # noqa: E402
from app.core.tasks import process_task as ptm  # noqa: E402
from app.core.observability import task_observation as obs_module  # noqa: E402
from app.models.task_observation import TaskFlow, TaskPhase  # noqa: E402


# ============================================================================
# B: 解析超时熔断
# ============================================================================


@pytest.mark.asyncio
async def test_run_parse_stage_timeout_raises_readable_error_and_resets_pool(
    monkeypatch,
):
    """解析超时时: 抛带“超时”字样的可读 TimeoutError, 且重置(终止)进程池一次。"""
    reset_calls: list[int] = []

    def _fake_reset() -> None:
        reset_calls.append(1)

    monkeypatch.setattr(ptm, "_reset_parse_executor", _fake_reset)
    # 不让真实进程池被创建
    monkeypatch.setattr(ptm, "_get_parse_executor", lambda: object())

    async def _fake_wait_for(awaitable, timeout):
        # 关闭未消费协程避免 "coroutine never awaited" 警告, 模拟超时
        if asyncio.iscoroutine(awaitable):
            awaitable.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(parse_stage_module.asyncio, "wait_for", _fake_wait_for)

    heartbeats: list[tuple] = []

    def _hb(stage: str, progress: str = "", extra=None) -> None:
        heartbeats.append((stage, progress))

    with pytest.raises(TimeoutError) as exc_info:
        await parse_stage_module.run_parse_stage(
            file_path="/tmp/huge.pdf",
            image_save_dir="/tmp/imgs",
            document_id=123,
            heartbeat=_hb,
        )

    # 错误信息必须可读且含“超时”, 供前端/时间线展示
    assert "超时" in str(exc_info.value)
    # 必须终止失控子进程(重置进程池)恰好一次
    assert reset_calls == [1]


@pytest.mark.asyncio
async def test_run_parse_stage_success_does_not_reset_pool(monkeypatch):
    """正常解析(未超时)路径不得误触发进程池重置。"""
    reset_calls: list[int] = []
    monkeypatch.setattr(
        ptm, "_reset_parse_executor", lambda: reset_calls.append(1)
    )
    monkeypatch.setattr(ptm, "_get_parse_executor", lambda: object())

    class _FakePage:
        page_num = 1
        text = "评分办法 第三章"
        tables: list = []
        images: list = []

    class _FakeParsed:
        page_count = 3
        all_images: list = []
        # 阶段0.3 完整性闸门: 假解析产物需带可用页(有文本), 否则被 fail-visible 拦下
        pages = [_FakePage()]

    fake_parsed = _FakeParsed()

    async def _fake_wait_for(awaitable, timeout):
        if asyncio.iscoroutine(awaitable):
            awaitable.close()
        return fake_parsed

    monkeypatch.setattr(parse_stage_module.asyncio, "wait_for", _fake_wait_for)

    # Session 2 保存 page_count: 桩掉 async_session_factory 与 _get_document
    class _FakeDoc:
        page_count = 0

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def commit(self):
            return None

    monkeypatch.setattr(ptm, "async_session_factory", lambda: _FakeSession())

    async def _fake_get_document(db, document_id):
        return _FakeDoc()

    monkeypatch.setattr(ptm, "_get_document", _fake_get_document)

    parsed_doc, image_count = await parse_stage_module.run_parse_stage(
        file_path="/tmp/ok.pdf",
        image_save_dir="/tmp/imgs",
        document_id=123,
        heartbeat=lambda *a, **k: None,
    )

    assert parsed_doc is fake_parsed
    assert image_count == 0
    assert reset_calls == []


# ============================================================================
# A: 阶段观测落库
# ============================================================================


@pytest.mark.asyncio
async def test_record_stage_persists_stage_event_same_chain(monkeypatch):
    """record_stage 写入 phase=STAGE, 且 task_id 与 observe_task 的 START/END 同链。"""
    captured: dict = {}

    async def _fake_record_task_observation(**kwargs):
        captured.update(kwargs)

    # _record 在调用时才 from app.services.task_observation_service import record_task_observation
    import app.services.task_observation_service as svc

    monkeypatch.setattr(svc, "record_task_observation", _fake_record_task_observation)

    await obs_module.record_stage(
        flow=TaskFlow.PROCESS,
        task_type="process",
        project_id=42,
        stage="parse",
        labels={"document_id": 7},
    )

    assert captured["phase"] == TaskPhase.STAGE
    assert captured["stage"] == "parse"
    assert captured["project_id"] == 42
    # 与 _TaskObservationContext 的 task_id 约定一致: {flow}:{project_id}:{task_type}
    assert captured["task_id"] == "process:42:process"
    assert captured["labels"] == {"document_id": 7}
