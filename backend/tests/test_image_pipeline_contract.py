import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from contextlib import asynccontextmanager

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")

from app.core.parser.image_extractor import (
    IMAGE_EVIDENCE_MARKER,
    is_annotation_chunk_content,
    is_evidence_chunk_content,
    is_image_related_chunk_content,
)
from app.core.collusion.analyzer import CollusionAnalyzer
from app.core.retriever.semantic_retriever import SearchResult, SemanticRetriever
from app.core.tasks import process_task as process_task_module
from app.core.tasks.process_task import _get_document_image_dir
from app.core.parser.image_extractor import ExtractedImage
from app.core.writer.material_content_extractor import MaterialContentExtractor
from app.models.chunk import Chunk
from app.models.document import DocumentType
from app.models.project import ProjectType


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
        # 兼容 get_vector_job_state 等读单行场景: 空列表返回 None
        return self._items[0] if self._items else None


def test_image_chunk_classification_helpers_cover_annotation_and_evidence() -> None:
    annotation = '[图片1: 大幅扫描件, VL识别: "营业执照"]'
    evidence = f"{IMAGE_EVIDENCE_MARKER} 本页提供了营业执照扫描件。"
    plain = "普通正文内容"

    assert is_annotation_chunk_content(annotation) is True
    assert is_annotation_chunk_content(evidence) is False
    assert is_evidence_chunk_content(evidence) is True
    assert is_evidence_chunk_content(annotation) is False
    assert is_image_related_chunk_content(annotation) is True
    assert is_image_related_chunk_content(evidence) is True
    assert is_image_related_chunk_content(plain) is False


def test_process_task_uses_document_scoped_image_dir() -> None:
    image_dir = _get_document_image_dir(
        upload_dir=Path("storage/uploads"),
        project_id=12,
        document_id=34,
    )
    assert str(image_dir).replace("\\", "/").endswith("/12/images/doc_34")


def test_material_image_evidence_summary_dedupes_lines() -> None:
    images = [
        SimpleNamespace(visual_content="营业执照\n统一社会信用代码: 123", spatial_context=""),
        SimpleNamespace(visual_content="营业执照\n统一社会信用代码: 123", spatial_context=""),
        SimpleNamespace(visual_content="", spatial_context="法定代表人身份证明"),
    ]

    summary = MaterialContentExtractor._build_material_image_evidence(images)

    assert "[素材图片证据摘要]" in summary
    assert summary.count("营业执照") == 1
    assert "法定代表人身份证明" in summary


def test_search_result_marks_image_related_by_chunk_type() -> None:
    result = SearchResult(
        chunk_id=1,
        document_id=2,
        content="普通文本",
        page_num=3,
        similarity=0.1,
        chunk_type="image_evidence",
    )
    assert SemanticRetriever.is_image_related_result(result) is True


@pytest.mark.asyncio
async def test_fetch_image_chunks_by_pages_query_includes_chunk_type_and_evidence_marker() -> None:
    captured = {}

    async def _execute(stmt):
        captured["sql"] = str(stmt)
        captured["params"] = stmt.compile().params
        return _FakeExecuteResult([])

    retriever = SemanticRetriever(SimpleNamespace(execute=_execute))
    retriever._get_document = AsyncMock(return_value=SimpleNamespace(doc_type="bid"))  # type: ignore[method-assign]

    await retriever.fetch_image_chunks_by_pages(
        document_id=9,
        page_nums={3},
    )

    sql = captured["sql"]
    params = captured["params"]
    assert "chunks.chunk_type" in sql
    assert "chunk_type_1" in params
    assert "image_annotation" in params["chunk_type_1"]
    assert "image_evidence" in params["chunk_type_1"]
    assert IMAGE_EVIDENCE_MARKER in params.values()


@pytest.mark.asyncio
async def test_fetch_image_chunks_globally_query_includes_chunk_type_and_evidence_marker() -> None:
    captured = {}

    async def _execute(stmt):
        captured["sql"] = str(stmt)
        captured["params"] = stmt.compile().params
        return _FakeExecuteResult([])

    retriever = SemanticRetriever(SimpleNamespace(execute=_execute))
    retriever._get_document = AsyncMock(return_value=SimpleNamespace(doc_type="bid"))  # type: ignore[method-assign]

    await retriever.fetch_image_chunks_globally(
        document_id=9,
        keywords=["营业执照"],
    )

    sql = captured["sql"]
    params = captured["params"]
    assert "chunks.chunk_type" in sql
    assert "chunk_type_1" in params
    assert "image_annotation" in params["chunk_type_1"]
    assert "image_evidence" in params["chunk_type_1"]
    assert IMAGE_EVIDENCE_MARKER in params.values()


@pytest.mark.asyncio
async def test_fetch_keyword_chunks_globally_searches_structured_text_and_section_path() -> None:
    """未响应兜底必须能搜索表格/正文chunk,不能只搜索图片证据。"""
    captured = {}

    async def _execute(stmt):
        captured["sql"] = str(stmt)
        captured["params"] = stmt.compile().params
        return _FakeExecuteResult([])

    retriever = SemanticRetriever(SimpleNamespace(execute=_execute))
    retriever._get_document = AsyncMock(return_value=SimpleNamespace(doc_type="bid"))  # type: ignore[method-assign]

    await retriever.fetch_keyword_chunks_globally(
        document_id=9,
        keywords=["正在施工", "项目情况表"],
    )

    sql = captured["sql"]
    params = captured["params"]
    assert "chunks.chunk_type" in sql
    assert "chunks.section_path" in sql
    assert "chunk_type_1" in params
    assert "table_row_with_header" in params["chunk_type_1"]
    assert "paragraph" in params["chunk_type_1"]


def test_search_result_from_chunk_preserves_chunk_type() -> None:
    chunk = Chunk(
        document_id=5,
        content=f"{IMAGE_EVIDENCE_MARKER} 本页提供了营业执照扫描件。",
        chunk_type="image_evidence",
        page_num=2,
        chunk_index=0,
        char_count=20,
    )
    chunk.id = 77

    result = SearchResult.from_chunk(chunk, similarity=0.42, doc_type=None)

    assert result.chunk_type == "image_evidence"
    assert result.chunk_id == 77


@pytest.mark.asyncio
async def test_collusion_document_text_keeps_annotation_and_evidence_sections(monkeypatch) -> None:
    rows = [
        SimpleNamespace(content="正文第一段", page_num=1),
        SimpleNamespace(content='[图片1: 大幅扫描件, VL识别: "营业执照"]', page_num=1),
        SimpleNamespace(content=f"{IMAGE_EVIDENCE_MARKER} 本页提供了营业执照扫描件。", page_num=1),
    ]

    fake_db = SimpleNamespace(execute=AsyncMock(return_value=_FakeExecuteResult(rows)))

    @asynccontextmanager
    async def fake_session_factory():
        yield fake_db

    monkeypatch.setattr(
        "app.core.collusion.analyzer.get_review_client",
        lambda project_id: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "app.core.collusion.analyzer_parts.extraction_stage.async_session_factory",
        fake_session_factory,
    )

    analyzer = CollusionAnalyzer(fake_db, project_id=1)
    text = await analyzer._get_document_text(doc_id=9, max_chars=400)

    assert "正文第一段" in text
    assert "图片附件的结构化标注结果" in text
    assert "图片附件的自然语言证据摘要" in text
    assert "营业执照" in text


def test_process_task_maps_project_type_to_image_business_flow() -> None:
    """投标审查/串标审查图片子任务必须使用不同业务 flow。"""

    assert process_task_module.business_flow_for_project_type(ProjectType.BID_REVIEW) == "bid_review"
    assert process_task_module.business_flow_for_project_type(ProjectType.COLLUSION_CHECK) == "collusion"


@pytest.mark.asyncio
async def test_process_task_runs_per_image_before_scan_full_page_and_preserves_scan_retry(monkeypatch) -> None:
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
        # ChunkPlanner.plan_parsed_pages 会遍历 page.tables, 需补空列表避免 AttributeError
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

    class _FakeDbSession:
        async def commit(self) -> None:
            return None

        async def flush(self) -> None:
            return None

        async def execute(self, stmt):
            return _FakeExecuteResult([])

        def add(self, obj) -> None:
            # upsert_vector_job_state 在无历史行时会 db.add(state), 测试桩可无操作
            return None

    @asynccontextmanager
    async def _fake_session_factory():
        yield _FakeDbSession()

    class _FakeLoop:
        async def run_in_executor(self, executor, func, file_path, image_dir):
            return parsed_doc

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

    call_sequence: list[tuple[str, list[int] | int]] = []
    full_page_results = ["", "scan-full-page"]

    async def _fake_recognize_images(*, images, **kwargs):
        call_sequence.append(("per_image", [img.page_num for img in images]))
        for img in images:
            img.visual_content = f"per-image-{img.page_num}"
        return SimpleNamespace(ocr_ok=0)

    async def _fake_analyze_full_page_subtasks(*, pages, **kwargs):
        page_nums = [page.page_num for page in pages]
        call_sequence.append(("full_page", page_nums))
        content = full_page_results.pop(0)
        if not content:
            return {}
        return {page_num: content for page_num in page_nums}

    monkeypatch.setattr(process_task_module, "async_session_factory", _fake_session_factory)
    monkeypatch.setattr(process_task_module, "_get_document", AsyncMock(return_value=document))
    monkeypatch.setattr(process_task_module, "_get_project", AsyncMock(return_value=project))
    monkeypatch.setattr(process_task_module, "_check_all_parsed", AsyncMock(return_value=False))
    monkeypatch.setattr(process_task_module, "_get_parse_executor", lambda: object())
    monkeypatch.setattr(process_task_module, "_render_pages_sync", lambda *args, **kwargs: {2: ("b64", "ctx")})
    monkeypatch.setattr(process_task_module.asyncio, "get_running_loop", lambda: _FakeLoop())
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
        _fake_recognize_images,
    )
    monkeypatch.setattr(
        "app.core.tasks.document_image_llm_task.analyze_full_pages_with_document_llm_subtasks",
        _fake_analyze_full_page_subtasks,
    )
    monkeypatch.setattr(
        "app.core.parser.image_analyzer.analyze_images",
        AsyncMock(side_effect=AssertionError("process_task must not call VL inline")),
    )
    monkeypatch.setattr(
        "app.core.parser.image_analyzer.analyze_full_page",
        AsyncMock(side_effect=AssertionError("process_task must not call full-page VL inline")),
    )
    monkeypatch.setattr("app.core.parser.TextChunker", _FakeChunker)
    monkeypatch.setattr("app.core.parser.toc_parser.TocParser", _FakeTocParser)
    monkeypatch.setattr("app.core.parser.toc_parser.serialize_toc_map", lambda toc_map: toc_map)
    monkeypatch.setattr("app.core.vectorizer.VectorIndexer", _FakeIndexer)
    monkeypatch.setattr("app.core.parser.adaptive_concurrency.AdaptiveConcurrency", lambda **kwargs: _FakeController())
    monkeypatch.setattr(
        "app.core.parser.adaptive_concurrency.RequestMetric",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )

    await process_task_module._run_process_task_inner(project_id=7, document_id=9, skip_vl=False)

    assert call_sequence[0] == ("per_image", [1])
    assert call_sequence[1] == ("full_page", [2])
    assert call_sequence[2] == ("full_page", [2])
    # 逐图识别结果不被整页结果覆盖
    assert per_image.visual_content == "per-image-1"
    # 整页识别结果落 ParsedPage.full_page_visual_content(页级单份),
    # 不再断言复制进每张图
    assert getattr(parsed_doc.pages[1], "full_page_visual_content", "") == "scan-full-page"
    assert document.parsed == 1
