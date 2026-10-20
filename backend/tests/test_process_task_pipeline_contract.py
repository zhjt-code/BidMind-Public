"""文档处理流水线契约防御测试

锁定 process_task 拆分前后必须保持一致的关键契约:
1. 模块级符号导出契约 -- 防 import 断裂(被外部测试/服务直接 import 的 13 个符号)
2. VectorJobState upsert 调用序列(status / stage)
3. Heartbeat stage 序列(parsing -> vl_recognition -> chunking -> vectorizing -> completed)
4. 防御: vectorize 异常时 parsed/chunks 一致性(要么都成功要么都不写)
5. 防御: ProjectCancelledError 走 cancelled 心跳, 不写 FAILED, 不写 VectorJobState
6. 防御: vl_keepalive callback 透传到下游 LLM 调用
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
from app.models.document import DocumentType  # noqa: E402
from app.models.project import ProjectType  # noqa: E402


# ============================================================================
# 共享 Fixture 桩
# ============================================================================


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
    def __init__(self):
        self.commits: list[None] = []

    async def commit(self) -> None:
        self.commits.append(None)

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


class _SuccessfulIndexer:
    """默认实现: index_chunks 返回非空列表(模拟向量落库成功)。"""

    def __init__(self, db):
        self.db = db

    async def delete_document_chunks(self, document_id: int) -> None:
        return None

    async def index_chunks(self, document_id: int, chunks, **kwargs):
        return [SimpleNamespace(id=1, document_id=document_id)]


class _EmptyIndexer:
    """index_chunks 返回空列表(模拟未生成任何有效向量)。"""

    def __init__(self, db):
        self.db = db

    async def delete_document_chunks(self, document_id: int) -> None:
        return None

    async def index_chunks(self, document_id: int, chunks, **kwargs):
        return []


def _build_minimal_parsed_doc() -> tuple[SimpleNamespace, ExtractedImage]:
    """构造最小 parsed_doc + 1 张 per_image, 不触发 scan_heavy 路径以收敛断言面。"""
    per_image = ExtractedImage(
        image_index=0,
        page_num=1,
        width=300,
        height=300,
        image_format="png",
        xref=101,
        file_path="normal.png",
    )
    parsed_doc = SimpleNamespace(
        page_count=1,
        all_images=[per_image],
        pages=[
            SimpleNamespace(
                page_num=1,
                text="这是普通图片页面，包含施工现场照片说明。",
                images=[per_image],
                tables=[],
            ),
        ],
    )
    return parsed_doc, per_image


def _install_common_patches(
    monkeypatch,
    *,
    parsed_doc,
    document,
    project,
    indexer_cls=_SuccessfulIndexer,
    upsert_recorder=None,
    heartbeat_recorder=None,
    per_image_fn=None,
    full_page_fn=None,
):
    """安装契约测试通用的 monkeypatch."""
    if upsert_recorder is None:
        upsert_recorder = []
    if heartbeat_recorder is None:
        heartbeat_recorder = []

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

    def _capture_heartbeat(task_name, stage="", progress="", extra=None):
        heartbeat_recorder.append((stage, progress, extra or {}))

    monkeypatch.setattr("app.core.tasks.update_heartbeat", _capture_heartbeat)

    async def _capture_upsert(db, **kwargs):
        upsert_recorder.append(
            {
                "status": kwargs.get("status"),
                "stage": kwargs.get("stage"),
                "queue_mode": kwargs.get("queue_mode"),
            }
        )

    # Patch 模块级和 vectorizer 内的 upsert (process_task 引用是 from-import)
    # 由于 process_task.py 用的是函数内 import, 这里 patch 源模块即可
    import app.core.vectorizer.job_state as job_state_module

    monkeypatch.setattr(job_state_module, "upsert_vector_job_state", _capture_upsert)

    # AI / VL / OCR 桩
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

    if per_image_fn is None:

        async def per_image_fn(*, images, **kwargs):
            for img in images:
                img.visual_content = f"per-image-{img.page_num}"
            return SimpleNamespace(ocr_ok=0)

    if full_page_fn is None:

        async def full_page_fn(*, pages, **kwargs):
            return {
                page.page_num: f"scan-full-page-{page.page_num}"
                for page in pages
            }

    monkeypatch.setattr(
        "app.core.tasks.document_image_llm_task.recognize_images_with_document_llm_subtasks",
        per_image_fn,
    )
    monkeypatch.setattr(
        "app.core.tasks.document_image_llm_task.analyze_full_pages_with_document_llm_subtasks",
        full_page_fn,
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
    monkeypatch.setattr("app.core.vectorizer.VectorIndexer", indexer_cls)
    monkeypatch.setattr(
        "app.core.parser.adaptive_concurrency.AdaptiveConcurrency",
        lambda **kwargs: _FakeController(),
    )
    monkeypatch.setattr(
        "app.core.parser.adaptive_concurrency.RequestMetric",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )

    return upsert_recorder, heartbeat_recorder


def _build_default_doc_project() -> tuple[SimpleNamespace, SimpleNamespace]:
    document = SimpleNamespace(
        file_path="demo.pdf",
        project_id=7,
        doc_type=DocumentType.BID,
        page_count=0,
        parsed=0,
        toc_map=None,
        ocr_image_count=0,
    )
    project = SimpleNamespace(
        project_type=ProjectType.BID_REVIEW,
        status=None,
        error_message="",
    )
    return document, project


# ============================================================================
# 契约 1: 模块级符号导出
# ============================================================================


def test_module_exports_public_and_private_symbols_required_by_external_callers() -> None:
    """process_task 模块必须保留下列符号供外部 import / monkeypatch.

    任一缺失即破坏外部调用方/测试契约, 必须立即修复。
    """
    required_symbols = [
        # 公共入口
        "run_process_task",
        "get_process_task_name",
        "resolve_effective_skip_vl",
        "business_flow_for_project_type",
        # 私有 helper (被外部测试 import)
        "_compute_scan_concurrency",
        "_get_document_image_dir",
        "_run_process_task_inner",
        # 外部测试 monkeypatch 的模块级符号
        "async_session_factory",
        "_get_document",
        "_get_project",
        "_check_all_parsed",
        "_get_parse_executor",
        "_render_pages_sync",
        # 异常类
        "_VLQualityError",
    ]
    missing = [name for name in required_symbols if not hasattr(process_task_module, name)]
    assert not missing, f"process_task 模块缺失外部依赖的符号: {missing}"


# ============================================================================
# 契约 2: VectorJobState upsert 序列(status + stage)
# ============================================================================


@pytest.mark.asyncio
async def test_vector_job_state_upsert_sequence_locked(monkeypatch) -> None:
    """VectorJobState upsert 必须按 RUNNING(parsing) -> RUNNING(vectorizing) -> COMPLETED 顺序执行。

 锁定 修复: 向量化启动 RUNNING 必须独立提交以释放行锁(便于 keepalive 节流写入)。
 """
    parsed_doc, _ = _build_minimal_parsed_doc()
    document, project = _build_default_doc_project()

    upsert_recorder: list[dict] = []
    _install_common_patches(
        monkeypatch,
        parsed_doc=parsed_doc,
        document=document,
        project=project,
        upsert_recorder=upsert_recorder,
    )

    await process_task_module._run_process_task_inner(
        project_id=7, document_id=9, skip_vl=False, queue_mode="asyncio"
    )

    # 锁定调用次数 = 3 (Session 1 启动 RUNNING parsing / Session 4a RUNNING vectorizing / Session 4b COMPLETED)
    statuses = [(r["status"].value if r["status"] else None, r["stage"]) for r in upsert_recorder]
    assert len(upsert_recorder) == 3, f"upsert 次数变化, 实际: {statuses}"
    assert statuses[0] == ("running", "parsing"), f"Session 1 状态错: {statuses[0]}"
    assert statuses[1] == ("running", "vectorizing"), f"Session 4a 状态错( 行锁释放): {statuses[1]}"
    assert statuses[2] == ("completed", "completed"), f"Session 4b 状态错: {statuses[2]}"


# ============================================================================
# 契约 3: Heartbeat stage 序列
# ============================================================================


@pytest.mark.asyncio
async def test_heartbeat_stage_sequence_covers_all_phases(monkeypatch) -> None:
    """心跳 stage 序列必须依次出现: parsing / vl_recognition / chunking / vectorizing / completed.

 前端依赖此序列驱动进度条, 任一缺失 = 进度条卡死(同源风险)。
 """
    parsed_doc, _ = _build_minimal_parsed_doc()
    document, project = _build_default_doc_project()

    heartbeat_recorder: list[tuple] = []
    _install_common_patches(
        monkeypatch,
        parsed_doc=parsed_doc,
        document=document,
        project=project,
        heartbeat_recorder=heartbeat_recorder,
    )

    await process_task_module._run_process_task_inner(
        project_id=7, document_id=9, skip_vl=False, queue_mode="asyncio"
    )

    stages = [stage for stage, _, _ in heartbeat_recorder]
    expected_in_order = ["parsing", "vl_recognition", "chunking", "vectorizing", "completed"]

    last_idx = -1
    for expected_stage in expected_in_order:
        try:
            idx = stages.index(expected_stage, last_idx + 1)
        except ValueError:
            raise AssertionError(
                f"心跳序列缺失 {expected_stage!r}, 已出现序列: {stages}"
            )
        last_idx = idx

    # 收尾阶段必须以 completed 结束
    assert stages[-1] == "completed", f"最终心跳必须为 completed, 实际: {stages[-1]}"


@pytest.mark.asyncio
async def test_document_qa_is_enqueued_as_job_after_processing(monkeypatch) -> None:
    """: 单文档质检只投递到处理队列, 处理链内不再就地计算。"""
    parsed_doc, _ = _build_minimal_parsed_doc()
    document, project = _build_default_doc_project()
    _install_common_patches(
        monkeypatch, parsed_doc=parsed_doc, document=document, project=project,
    )
    enqueue = AsyncMock(return_value="queued")
    monkeypatch.setattr("app.core.tasks.document_qa_job.enqueue_document_qa", enqueue)
    monkeypatch.setattr(
        "app.core.tasks.process.document_qa_stage.run_document_qa_stage",
        AsyncMock(side_effect=AssertionError("处理链不得就地运行质检")),
    )

    await process_task_module._run_process_task_inner(
        project_id=7, document_id=9, skip_vl=False, queue_mode="asyncio"
    )

    enqueue.assert_awaited_once_with(7, 9)


# ============================================================================
# 契约 4: 原子事务 -- vectorize 异常时 parsed/chunks 不被部分写入
# ============================================================================


@pytest.mark.asyncio
async def test_vectorize_exception_does_not_mark_parsed_partially(monkeypatch) -> None:
    """ 防御: index_chunks 抛异常时, document.parsed 必须保持 0(原子事务回滚语义)。

 根因: process_task Session 4 将 commit 错置在 async with 之外导致跨会话不可见,
 串标分析器在空输入兜底走 inconclusive 但 status 标 COMPLETED, 形成"假完成"。
 修复后: chunks 写库 / parsed=1 / VectorJobState COMPLETED 必须在同一 async with 块内原子提交。
 """
    parsed_doc, _ = _build_minimal_parsed_doc()
    document, project = _build_default_doc_project()

    class _FailingIndexer:
        def __init__(self, db):
            self.db = db

        async def index_chunks(self, document_id: int, chunks, **kwargs):
            raise RuntimeError("simulated vectorize failure")

    upsert_recorder: list[dict] = []
    heartbeat_recorder: list[tuple] = []
    _install_common_patches(
        monkeypatch,
        parsed_doc=parsed_doc,
        document=document,
        project=project,
        indexer_cls=_FailingIndexer,
        upsert_recorder=upsert_recorder,
        heartbeat_recorder=heartbeat_recorder,
    )

    # 异常应被外层 except 捕获并走 FAILED 路径, 不向上抛
    await process_task_module._run_process_task_inner(
        project_id=7, document_id=9, skip_vl=False, queue_mode="asyncio"
    )

    # 关键断言: parsed 不可被设为 1 (原子事务边界保护)
    assert document.parsed == 0, "vectorize 异常时 parsed 必须保持 0(原子事务边界破)"

    # 心跳必须以 failed 结束, 不可遗留 vectorizing 状态
    final_stage = heartbeat_recorder[-1][0]
    assert final_stage == "failed", f"vectorize 异常后心跳必须为 failed, 实际: {final_stage}"

    # VectorJobState 必须有 FAILED 写入(非空但 COMPLETED 不在序列末尾)
    statuses = [r["status"].value if r["status"] else None for r in upsert_recorder]
    assert "failed" in statuses, f"vectorize 异常后必须写入 FAILED, 实际状态序列: {statuses}"
    assert statuses[-1] == "failed", f"最终 VectorJobState 必须为 FAILED, 实际: {statuses[-1]}"


@pytest.mark.asyncio
async def test_empty_vectorize_result_does_not_mark_parsed_or_completed(monkeypatch) -> None:
    """ 防御: index_chunks 返回空列表时不可写 parsed=1 或 COMPLETED。

 0 chunk/0 embedding 不是成功处理结果, 否则串标启动只看 parsed 时会把空输入带入分析。
 """
    parsed_doc, _ = _build_minimal_parsed_doc()
    document, project = _build_default_doc_project()

    upsert_recorder: list[dict] = []
    heartbeat_recorder: list[tuple] = []
    _install_common_patches(
        monkeypatch,
        parsed_doc=parsed_doc,
        document=document,
        project=project,
        indexer_cls=_EmptyIndexer,
        upsert_recorder=upsert_recorder,
        heartbeat_recorder=heartbeat_recorder,
    )

    await process_task_module._run_process_task_inner(
        project_id=7, document_id=9, skip_vl=False, queue_mode="asyncio"
    )

    assert document.parsed == 0, "0 向量结果不得标记 document.parsed=1"
    assert heartbeat_recorder[-1][0] == "failed"
    statuses = [r["status"].value if r["status"] else None for r in upsert_recorder]
    assert statuses[-1] == "failed", f"0 向量结果必须以 FAILED 收尾, 实际: {statuses}"


# ============================================================================
# 契约 5: 取消语义 -- ProjectCancelledError 走 cancelled, 不写 FAILED
# ============================================================================


@pytest.mark.asyncio
async def test_project_cancelled_error_routes_to_cancelled_not_failed(monkeypatch) -> None:
    """ 防御: PDF 解析阶段命中 raise_if_project_cancelled, 必须走 cancelled 路径,
 不可写 FAILED, 不可写 VectorJobState (避免外键失败)。
 """
    parsed_doc, _ = _build_minimal_parsed_doc()
    document, project = _build_default_doc_project()

    upsert_recorder: list[dict] = []
    heartbeat_recorder: list[tuple] = []
    _install_common_patches(
        monkeypatch,
        parsed_doc=parsed_doc,
        document=document,
        project=project,
        upsert_recorder=upsert_recorder,
        heartbeat_recorder=heartbeat_recorder,
    )

    # 模拟 PDF 解析后立即被取消(在 raise_if_project_cancelled 处抛)
    # 由于 raise_if_project_cancelled 在 process_task 内是函数内 import,
    # 必须 patch 源模块 app.core.arq_runtime
    import app.core.arq_runtime as arq_runtime_module
    from app.core.arq_runtime import ProjectCancelledError

    cancel_calls = {"count": 0}

    def _raise_cancel(project_id: int) -> None:
        cancel_calls["count"] += 1
        # 第 2 次调用(阶段 2 入口)抛取消异常, 模拟用户在解析完成后取消
        if cancel_calls["count"] >= 2:
            raise ProjectCancelledError(project_id)

    monkeypatch.setattr(arq_runtime_module, "raise_if_project_cancelled", _raise_cancel)
    # is_project_cancelled 在最外层入口检查, 必须返回 False 让任务真正启动
    monkeypatch.setattr(arq_runtime_module, "is_project_cancelled", lambda pid: False)

    await process_task_module._run_process_task_inner(
        project_id=7, document_id=9, skip_vl=False, queue_mode="asyncio"
    )

    # 心跳必须以 cancelled 结束, 不可走 failed
    final_stage = heartbeat_recorder[-1][0]
    assert final_stage == "cancelled", f"取消路径心跳必须为 cancelled, 实际: {final_stage}"
    stages = [stage for stage, _, _ in heartbeat_recorder]
    assert "failed" not in stages, f"取消路径不可出现 failed 心跳, 实际: {stages}"

    # VectorJobState 不可有 FAILED 写入(外键失败防御)
    statuses = [r["status"].value if r["status"] else None for r in upsert_recorder]
    assert "failed" not in statuses, f"取消路径不可写 VectorJobState FAILED, 实际: {statuses}"


# ============================================================================
# 契约 6: keepalive callback 透传到下游 LLM 调用
# ============================================================================


@pytest.mark.asyncio
async def test_vl_keepalive_callback_propagates_to_downstream_llm(monkeypatch) -> None:
    """ 防御: vl_keepalive callback 必须透传到 recognize_images_with_document_llm_subtasks
 与 analyze_full_pages_with_document_llm_subtasks, 否则 ProcessWorker 长 batch 等待期间
 无法刷新业务心跳, 触发 API 误判 running_heartbeat_stale。
 """
    parsed_doc, _ = _build_minimal_parsed_doc()
    scan_image = ExtractedImage(
        image_index=0,
        page_num=2,
        width=600,
        height=800,
        image_format="png",
        xref=202,
        file_path="scan.png",
    )
    parsed_doc.page_count = 2
    parsed_doc.all_images.append(scan_image)
    parsed_doc.pages.append(
        SimpleNamespace(page_num=2, text="", images=[scan_image], tables=[])
    )
    document, project = _build_default_doc_project()

    captured_kwargs: dict[str, dict] = {}

    async def per_image_capturing(*, images, **kwargs):
        captured_kwargs["per_image"] = kwargs
        for img in images:
            img.visual_content = f"per-image-{img.page_num}"
        return SimpleNamespace(ocr_ok=0)

    async def full_page_capturing(*, pages, **kwargs):
        captured_kwargs.setdefault("full_page", kwargs)
        return {
            page.page_num: f"scan-full-page-{page.page_num}"
            for page in pages
        }

    _install_common_patches(
        monkeypatch,
        parsed_doc=parsed_doc,
        document=document,
        project=project,
        per_image_fn=per_image_capturing,
        full_page_fn=full_page_capturing,
    )

    await process_task_module._run_process_task_inner(
        project_id=7, document_id=9, skip_vl=False, queue_mode="asyncio"
    )

    # per_image 必须收到 keepalive_callback
    assert "per_image" in captured_kwargs, "per_image 阶段未被调用"
    keepalive = captured_kwargs["per_image"].get("keepalive_callback")
    assert keepalive is not None, " 防御: per_image 未透传 keepalive_callback"
    assert callable(keepalive), "keepalive_callback 必须可调用"
    # 调用一次确保不抛(回调内部异常应被吞掉)
    keepalive()
    assert "full_page" in captured_kwargs, "full_page 阶段未被调用"
    full_page_keepalive = captured_kwargs["full_page"].get("keepalive_callback")
    assert full_page_keepalive is keepalive, " 防御: full_page 未透传同一 keepalive_callback"


# ============================================================================
# 契约 7: 图片识别用量与来源必须随 parsed=1 持久化
# ============================================================================


@pytest.mark.asyncio
async def test_ocr_count_and_image_provenance_persisted_on_document(monkeypatch) -> None:
    """ 防御: per_image OCR 成功计数与图片来源必须写入 Document。

 锁定链路: vl_stage stats.ocr_ok -> 编排器 DocumentImageOutcome ->
 vectorize_stage 同一事务写 ocr_image_count / image_count / vl_skipped。
 任一环节丢失 => 消耗记录 OCR 维度静默归零, 且无法与"跳过/无图片"区分。
 """
    parsed_doc, _ = _build_minimal_parsed_doc()
    document, project = _build_default_doc_project()

    async def per_image_with_ocr(*, images, **kwargs):
        for img in images:
            img.visual_content = f"ocr-{img.page_num}"
        return SimpleNamespace(ocr_ok=1)

    _install_common_patches(
        monkeypatch,
        parsed_doc=parsed_doc,
        document=document,
        project=project,
        per_image_fn=per_image_with_ocr,
    )

    await process_task_module._run_process_task_inner(
        project_id=7, document_id=9, skip_vl=False, queue_mode="asyncio"
    )

    assert document.parsed == 1
    assert document.ocr_image_count == 1, "OCR 成功计数必须持久化到 Document"
    assert document.image_count == 1, "解析图片总数必须持久化到 Document"
    assert document.vl_skipped is False, "未跳过识别时 vl_skipped 必须为 False"


@pytest.mark.asyncio
async def test_skip_vl_persists_skip_provenance_with_zero_ocr(monkeypatch) -> None:
    """ 防御: 跳过图片识别时 vl_skipped=True 且 OCR 计数为 0。

 消耗记录靠该来源标记区分"用户跳过"与"计数丢失", 缺失即回归零值歧义。
 """
    parsed_doc, _ = _build_minimal_parsed_doc()
    document, project = _build_default_doc_project()
    # BID_REVIEW + TENDER 尊重 requested_skip_vl(BID 会被强制 False)
    document.doc_type = DocumentType.TENDER

    vl_calls = {"count": 0}

    async def per_image_must_not_run(*, images, **kwargs):
        vl_calls["count"] += 1
        return SimpleNamespace(ocr_ok=99)

    _install_common_patches(
        monkeypatch,
        parsed_doc=parsed_doc,
        document=document,
        project=project,
        per_image_fn=per_image_must_not_run,
    )

    await process_task_module._run_process_task_inner(
        project_id=7, document_id=9, skip_vl=True, queue_mode="asyncio"
    )

    assert vl_calls["count"] == 0, "跳过策略下不得调用图片识别"
    assert document.parsed == 1
    assert document.ocr_image_count == 0
    assert document.image_count == 1, "跳过识别不影响解析图片总数记录"
    assert document.vl_skipped is True, "跳过来源必须持久化"
