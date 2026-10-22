"""文档处理阶段 2 (per_image / scan_heavy) 并行执行防御测试。

防御目标:
- per_image 与 scan_heavy 两阶段必须能并发交错启动, 不能串行阻塞
- 任一阶段抛异常不得 cancel 另一阶段, 整个流程仍要进入下游
- scan_heavy 整页 OCR 并发必须为 per_image 阶段预留 1 个 OCR 槽,
 否则两阶段共抢同一 OCR 池, 队列堆积 -> is_congested -> 整页全员降级 VL
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")

from app.core.parser.image_extractor import ExtractedImage  # noqa: E402
from app.core.tasks import process_task as process_task_module  # noqa: E402
from app.core.tasks.process_task import _compute_scan_concurrency  # noqa: E402
from app.models.document import DocumentType  # noqa: E402
from app.models.project import ProjectType  # noqa: E402


class _FakeScalarResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _FakeExecuteResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return _FakeScalarResult(self._items)

    def fetchall(self):
        return self._items

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None


class _FakeDbSession:
    async def commit(self) -> None:
        return None

    async def flush(self) -> None:
        return None

    async def execute(self, stmt):
        return _FakeExecuteResult([])

    def add(self, obj) -> None:
        return None


@asynccontextmanager
async def _fake_session_factory():
    yield _FakeDbSession()


class _FakeLoop:
    def __init__(self, parsed_doc):
        self._parsed_doc = parsed_doc

    async def run_in_executor(self, executor, func, file_path, image_dir):
        return self._parsed_doc


class _WeightedContext:
    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeController:
    current = 1

    def weighted(self, weight: int = 1):
        return _WeightedContext()

    async def record(self, metric) -> None:
        return None

    def get_stats(self) -> dict:
        return {"concurrency": 1, "p95_ms": 0, "last_adjust": ""}

    def reset_window(self) -> None:
        return None


class _FakeChunker:
    def split_with_page_tracking(self, pages):
        return []


class _FakeTocParser:
    def parse(self, pages):
        return None


class _FakeIndexer:
    def __init__(self, db):
        self.db = db

    async def delete_document_chunks(self, document_id: int) -> None:
        return None

    async def index_chunks(self, document_id: int, chunks, **kwargs):
            return [SimpleNamespace(id=1, document_id=document_id)]


def _build_fixture(monkeypatch, *, per_image_fn, scan_full_page_fn):
    """构造 _run_process_task_inner 所需的最小 fixture。"""
    per_image = ExtractedImage(
        image_index=0,
        page_num=1,
        width=300,
        height=300,
        image_format="png",
        xref=101,
        file_path="normal.png",
    )
    scan_image = ExtractedImage(
        image_index=0,
        page_num=2,
        width=600,
        height=800,
        image_format="png",
        xref=202,
        file_path="scan.png",
    )
    parsed_doc = SimpleNamespace(
        page_count=2,
        all_images=[per_image, scan_image],
        pages=[
            SimpleNamespace(
                page_num=1,
                text="这是普通图片页面，包含施工现场照片说明。",
                images=[per_image],
                tables=[],
            ),
            SimpleNamespace(page_num=2, text="", images=[scan_image], tables=[]),
        ],
    )

    document = SimpleNamespace(
        file_path="demo.pdf",
        project_id=7,
        doc_type=DocumentType.BID,
        page_count=0,
        parsed=0,
        toc_map=None,
    )
    project = SimpleNamespace(
        project_type=ProjectType.BID_REVIEW,
        status=None,
        error_message="",
    )

    monkeypatch.setattr(process_task_module, "async_session_factory", _fake_session_factory)
    monkeypatch.setattr(process_task_module, "_get_document", AsyncMock(return_value=document))
    monkeypatch.setattr(process_task_module, "_get_project", AsyncMock(return_value=project))
    monkeypatch.setattr(process_task_module, "_check_all_parsed", AsyncMock(return_value=False))
    monkeypatch.setattr(process_task_module, "_get_parse_executor", lambda: object())
    monkeypatch.setattr(
        process_task_module,
        "_render_pages_sync",
        lambda *args, **kwargs: {2: ("b64", "ctx")},
    )
    monkeypatch.setattr(
        process_task_module.asyncio,
        "get_running_loop",
        lambda: _FakeLoop(parsed_doc),
    )
    monkeypatch.setattr("app.core.tasks.update_heartbeat", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "app.core.ai_settings.load_ai_settings",
        lambda: SimpleNamespace(
            vision_model_id="vision-main",
            get_config_by_id=lambda *_args, **_kwargs: SimpleNamespace(config_type="local"),
        ),
    )
    monkeypatch.setattr("app.core.ai.ai_factory.get_vision_dispatcher", lambda: None)
    monkeypatch.setattr("app.core.parser.image_recognition_service.get_ocr_engine", lambda: None)
    monkeypatch.setattr(
        "app.core.tasks.document_image_llm_task.probe_document_vl_availability",
        AsyncMock(return_value=(True, "")),
    )
    monkeypatch.setattr(
        "app.core.tasks.document_image_llm_task.recognize_images_with_document_llm_subtasks",
        per_image_fn,
    )
    monkeypatch.setattr(
        "app.core.tasks.document_image_llm_task.analyze_full_pages_with_document_llm_subtasks",
        scan_full_page_fn,
    )
    monkeypatch.setattr(
        "app.core.parser.image_analyzer.analyze_images",
        AsyncMock(side_effect=AssertionError("must not call inline VL")),
    )
    monkeypatch.setattr(
        "app.core.parser.image_analyzer.analyze_full_page",
        AsyncMock(side_effect=AssertionError("must not call inline full-page VL")),
    )
    monkeypatch.setattr("app.core.parser.TextChunker", _FakeChunker)
    monkeypatch.setattr("app.core.parser.toc_parser.TocParser", _FakeTocParser)
    monkeypatch.setattr(
        "app.core.parser.toc_parser.serialize_toc_map",
        lambda toc_map: toc_map,
    )
    monkeypatch.setattr("app.core.vectorizer.VectorIndexer", _FakeIndexer)
    monkeypatch.setattr(
        "app.core.parser.adaptive_concurrency.AdaptiveConcurrency",
        lambda **kwargs: _FakeController(),
    )
    monkeypatch.setattr(
        "app.core.parser.adaptive_concurrency.RequestMetric",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )
    return per_image, scan_image, document


@pytest.mark.asyncio
async def test_phases_run_concurrently_not_serially(monkeypatch) -> None:
    """两阶段必须并发: 用互相等待的 Event 模拟, 串行实现会超时死锁。"""
    per_image_started = asyncio.Event()
    scan_full_page_started = asyncio.Event()

    async def _per_image(*, images, **kwargs):
        per_image_started.set()
        # 必须等 scan_heavy 真正启动后再返回,
        # 串行实现 scan_heavy 在 per_image 之后才启动 -> 此处永远等不到 -> 超时
        await asyncio.wait_for(scan_full_page_started.wait(), timeout=2.0)
        for img in images:
            img.visual_content = f"per-image-{img.page_num}"
        return SimpleNamespace(ocr_ok=0)

    async def _scan_full_page(*, pages, **kwargs):
        scan_full_page_started.set()
        await asyncio.sleep(0)
        return {page.page_num: f"scan-full-page-{page.page_num}" for page in pages}

    per_image, scan_image, document = _build_fixture(
        monkeypatch,
        per_image_fn=_per_image,
        scan_full_page_fn=_scan_full_page,
    )

    # 整体限时 5s 内必须完成; 串行实现下 per_image 永远等不到 scan_heavy
    # 启动信号, 触发 wait_for 超时, 测试失败
    await asyncio.wait_for(
        process_task_module._run_process_task_inner(
            project_id=7, document_id=9, skip_vl=False
        ),
        timeout=5.0,
    )

    assert per_image_started.is_set()
    assert scan_full_page_started.is_set()
    assert per_image.visual_content == "per-image-1"
    assert scan_image.visual_content == "scan-full-page-2"


@pytest.mark.asyncio
async def test_one_phase_exception_does_not_cancel_other(monkeypatch) -> None:
    """per_image 阶段抛异常, scan_heavy 仍要执行并完成, 文档下游处理仍要继续。"""
    scan_completed = asyncio.Event()

    async def _per_image_failing(*, images, **kwargs):
        raise RuntimeError("simulated per_image crash")

    async def _scan_full_page(*, pages, **kwargs):
        # 引入一次让权, 验证不会被 per_image 异常 cancel
        await asyncio.sleep(0)
        scan_completed.set()
        return {page.page_num: f"scan-ok-{page.page_num}" for page in pages}

    _per_image_img, scan_image, document = _build_fixture(
        monkeypatch,
        per_image_fn=_per_image_failing,
        scan_full_page_fn=_scan_full_page,
    )

    # 不应抛出: gather(return_exceptions=True) 吞掉异常并 logger.warning
    await process_task_module._run_process_task_inner(
        project_id=7, document_id=9, skip_vl=False
    )

    assert scan_completed.is_set(), "scan_heavy 阶段被错误地 cancel 了"
    assert scan_image.visual_content == "scan-ok-2"
    # 下游处理未中断: document 状态推进到 parsed=1
    assert document.parsed == 1


# ============================================================================
# scan_heavy 整页 OCR 并发必须为 per_image 阶段预留 OCR 槽
# 防御目标: 公式必须保证 per_image (1 槽) + scan_heavy (≤2 槽) ≤ 池容量,
#          避免两阶段共抢导致队列拥堵 -> is_congested -> 整页全降级 VL
# ============================================================================


def test_compute_scan_concurrency_zero_pool_uses_safe_default() -> None:
    """池信息不可用时(pool=0)兜底为 2, 作为本地 waiter 洪峰软界。"""
    assert _compute_scan_concurrency(0) == 2
    assert _compute_scan_concurrency(-1) == 2


def test_compute_scan_concurrency_single_instance_does_not_starve() -> None:
    """池只有 1 个健康实例时仍保持 2 路本地软界, 由公平限流器收敛。"""
    assert _compute_scan_concurrency(1) == 2


def test_compute_scan_concurrency_two_instances_matches_pool() -> None:
    """池=2 时允许整页阶段产生 2 个 waiter, 真实配额由流程限流器控制。"""
    assert _compute_scan_concurrency(2) == 2


def test_compute_scan_concurrency_typical_three_instance_pool() -> None:
    """典型场景: 池=3 时单流程可让三个实例都参与整页 OCR。"""
    assert _compute_scan_concurrency(3) == 3


def test_compute_scan_concurrency_tracks_large_pool() -> None:
    """池>3 时不再硬封顶 2, 单流程可吃满健康实例。"""
    assert _compute_scan_concurrency(4) == 4
    assert _compute_scan_concurrency(8) == 8
    assert _compute_scan_concurrency(100) == 100


def test_compute_scan_concurrency_invariant_is_at_least_pool_size() -> None:
    """不变量: 任意 pool_healthy >= 2 时, 整页 waiter 上限不小于池容量。

 目标是单流程吃满所有实例; 的 per_image 优先级由
 OcrFlowScheduler 的类内优先规则承接, 不再由静态 scan 上限预留槽位。
 """
    for pool in range(2, 20):
        scan_c = _compute_scan_concurrency(pool)
        assert scan_c >= pool, (
            f"pool_healthy={pool} 时 scan_concurrency={scan_c} 未允许吃满池容量"
        )
