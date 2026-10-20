from tests.synthetic_data import synthetic_text
import pytest
from types import SimpleNamespace

from app.core.collusion.knowledge_base import build_dynamic_normal_similarity_hint
from app.core.exceptions import ServiceError
from app.core.knowledge.atomizer import KnowledgeAtomizer
from app.core.knowledge.context import KnowledgeContextBuilder
from app.core.knowledge.usage_config import (
    build_usage_payload,
    parse_usage_config,
)
from app.models.kb_document import KbDocument
from app.services.knowledge_base_service import KnowledgeService
from app.schemas.knowledge_base import KnowledgeChunkReference


def test_knowledge_context_detects_forceful_conflict() -> None:
    builder = KnowledgeContextBuilder(db=None)  # type: ignore[arg-type]
    refs = [
        KnowledgeChunkReference(
            kb_doc_id=1,
            kb_chunk_id=2,
            page_num=3,
            title="企业经验",
            authority="user_experience",
            version="1",
            similarity=0.8,
            content="通常应当提供承诺函。",
        )
    ]

    note = builder._detect_conflict("招标文件明确禁止提供替代承诺函。", refs)

    assert note
    assert "冲突" in note


@pytest.mark.asyncio
async def test_dynamic_kb_hint_disabled_when_no_authorized_ids() -> None:
    """串标动态弱提示在未授权资料 IDs 时必须返回空字符串，避免默认注入 Prompt。"""
    hint = await build_dynamic_normal_similarity_hint(
        db=None,  # type: ignore[arg-type]
        chunk_pairs=[{"text_a": "报价格式模板", "text_b": "报价格式模板"}],
        owner_id=99,
        allowed_document_ids=[],
    )
    assert hint == ""


@pytest.mark.asyncio
async def test_kb_search_without_allowed_ids_returns_empty() -> None:
    """KnowledgeService.search 收到空 allowed_document_ids 时应直接降级为空。"""
    result = await KnowledgeService(db=None).search(  # type: ignore[arg-type]
        query="营业执照",
        owner_id=42,
        allowed_document_ids=[],
    )
    assert result == []


def test_build_usage_payload_disables_when_no_ids_selected() -> None:
    """启用但未选资料应当按未启用清空，避免 confirmed_by_user 误为 True。"""
    payload = build_usage_payload(
        enabled=True,
        flow="bid_review",
        selected_kb_document_ids=[],
    )
    assert payload["enabled"] is False
    assert payload["selected_kb_document_ids"] == []
    assert payload["confirmed_by_user"] is False


def test_build_usage_payload_normalizes_ids_and_marks_confirmed() -> None:
    """选择有效资料后必须设置 confirmed_by_user=True 并去重 ID。"""
    payload = build_usage_payload(
        enabled=True,
        flow="collusion_check",
        selected_kb_document_ids=[1, 1, 2, "3", "bad"],
    )
    assert payload["enabled"] is True
    assert payload["flow"] == "collusion_check"
    assert payload["selected_kb_document_ids"] == [1, 2, 3]
    assert payload["confirmed_by_user"] is True
    assert payload["usage_policy"] == "normal_similarity_exemption_only"


def test_parse_usage_config_active_only_for_matching_flow() -> None:
    """配置必须仅对匹配 flow 生效，跨 flow 禁止重用授权。"""
    payload = build_usage_payload(
        enabled=True,
        flow="bid_review",
        selected_kb_document_ids=[5, 6],
    )
    usage = parse_usage_config(payload)
    assert usage.is_active_for_flow("bid_review") is True
    assert usage.is_active_for_flow("collusion_check") is False


@pytest.mark.asyncio
async def test_collusion_dynamic_kb_hint_is_weak_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeKnowledgeService:
        def __init__(self, db: object) -> None:
            self.db = db

        async def search(self, **kwargs: object) -> list[KnowledgeChunkReference]:
            assert kwargs["applicable_flow"] == "collusion_check"
            return [
                KnowledgeChunkReference(
                    kb_doc_id=10,
                    kb_chunk_id=20,
                    page_num=5,
                    title="行业模板",
                    authority="industry_standard",
                    version="2026",
                    similarity=0.91,
                    content="该段属于行业通用报价格式模板。",
                )
            ]

    monkeypatch.setattr(
        "app.services.knowledge_base_service.KnowledgeService",
        FakeKnowledgeService,
    )

    hint = await build_dynamic_normal_similarity_hint(
        db=None,  # type: ignore[arg-type]
        chunk_pairs=[{"text_a": "报价格式模板", "text_b": "报价格式模板"}],
        owner_id=99,
        allowed_document_ids=[10],
    )

    assert "动态知识库弱提示" in hint
    assert "禁止作为串标定性依据" in hint
    assert "kb_doc_id=10" in hint


@pytest.mark.asyncio
async def test_knowledge_service_reuses_writer_material_extractor(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    image_path = tmp_path / "营业执照.jpg"
    image_path.write_bytes(b"fake-image")

    class FakeMaterialContent:
        text = synthetic_text('营业执照\n', '统一社会信用代码:', ' 91110000', 'XXXXXXXXXX')
        page_count = 1
        page_texts = [(1, text)]

    class FakeExtractor:
        def __init__(self, enable_vl: bool = True) -> None:
            assert enable_vl is True
            self.batch_called = False

        def parse_only(self, file_path: str, ext: str, image_save_dir: object) -> object:
            assert file_path == str(image_path)
            assert ext == ".jpg"
            assert image_save_dir is not None
            return SimpleNamespace(images=[SimpleNamespace(visual_content="营业执照")])

        async def batch_vl_recognize(self, images: list, project_id: int) -> None:
            assert len(images) == 1
            assert project_id == 0
            self.batch_called = True

        def assemble_with_vl(self, parse_result: object) -> FakeMaterialContent:
            assert self.batch_called is True
            return FakeMaterialContent()

    monkeypatch.setattr(
        "app.core.writer.material_content_extractor.MaterialContentExtractor",
        FakeExtractor,
    )

    page_texts, page_count, full_text = await KnowledgeService(db=None)._extract_content(image_path, 42)  # type: ignore[arg-type]

    assert page_count == 1
    assert page_texts[0][0] == 1
    assert "营业执照" in full_text


@pytest.mark.asyncio
async def test_knowledge_search_without_user_scope_returns_empty() -> None:
    result = await KnowledgeService(db=None).search(query="营业执照")  # type: ignore[arg-type]

    assert result == []


@pytest.mark.asyncio
async def test_knowledge_atomizer_generates_page_atoms_for_text(tmp_path) -> None:
    text_path = tmp_path / "公司简介.md"
    text_path.write_text("公司简介\n长期服务政企客户。", encoding="utf-8")

    result = await KnowledgeAtomizer().atomize(
        file_path=text_path,
        document_id=1,
        image_save_dir=tmp_path / "images",
        title="公司简介",
        business_type="company_profile",
    )

    assert result.full_text
    assert len(result.atoms) == 1
    assert result.atoms[0].business_type == "company_profile"


@pytest.mark.asyncio
async def test_projection_copies_existing_kb_chunks_without_reindex() -> None:
    class FakeDb:
        def __init__(self) -> None:
            self.next_id = 100
            self.added = []

        def add(self, obj: object) -> None:
            self.added.append(obj)
            setattr(obj, "id", self.next_id)
            self.next_id += 1

        async def flush(self) -> None:
            pass

    svc = KnowledgeService(FakeDb())  # type: ignore[arg-type]
    chunks = [
        SimpleNamespace(
            id=7,
            content="公司简介",
            embedding=[0.1, 0.2],
            chunk_fingerprint="fp-7",
            embedding_version="bge-m3-v1",
            parent_block_id="p1",
            section_path="简介",
            page_num=3,
        )
    ]

    chunk_ids = await svc._copy_kb_chunks_to_writing_material(  # type: ignore[attr-defined]
        material_id=1,
        project_id=2,
        chunks=chunks,
    )

    assert chunk_ids == [100]
    assert svc.db.added[0].embedding == [0.1, 0.2]  # type: ignore[attr-defined]
    assert svc.db.added[0].chunk_fingerprint == "fp-7"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_process_document_dispatches_background_job(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDb:
        async def commit(self) -> None:
            pass

        async def refresh(self, doc: object) -> None:
            pass

    doc = SimpleNamespace(id=7, processing_status="saved", error_message="old")
    svc = KnowledgeService(FakeDb())  # type: ignore[arg-type]

    async def fake_get_document(document_id: int, current_user: object) -> object:
        assert document_id == 7
        return doc

    dispatched: dict[str, object] = {}

    async def fake_dispatch_background_job(**kwargs: object) -> tuple[str, str | None]:
        dispatched.update(kwargs)
        return "asyncio_fallback", None

    monkeypatch.setattr(svc, "get_document", fake_get_document)
    monkeypatch.setattr("app.core.tasks.is_task_active", lambda name: False)
    monkeypatch.setattr("app.core.tasks.dispatch_background_job", fake_dispatch_background_job)

    result = await svc.process_document(7, current_user=object())  # type: ignore[arg-type]

    assert result is doc
    assert doc.processing_status == "processing"
    assert doc.error_message is None
    assert dispatched["arq_function"] == "knowledge_base_process_job"
    assert dispatched["arq_args"] == (7,)


# ---------------------------------------------------------------------------
# 防御测试: 删除接口与长任务的外键一致性
# ---------------------------------------------------------------------------


class _DeleteGuardDb:
    """仅覆盖删除路径所需的最小 AsyncSession 接口。"""

    def __init__(self, doc: object) -> None:
        self._doc = doc
        self.delete_called = False
        self.flush_called = False

    async def get(self, model: object, document_id: int) -> object:
        return self._doc

    async def delete(self, obj: object) -> None:
        self.delete_called = True

    async def flush(self) -> None:
        self.flush_called = True


@pytest.mark.asyncio
async def test_delete_document_blocks_when_processing_status_is_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """处理中的知识资料禁止删除, 防止后台任务撞外键。"""
    doc = SimpleNamespace(
        id=99,
        owner_id=1,
        processing_status="processing",
        file_path=None,
    )
    db = _DeleteGuardDb(doc)
    svc = KnowledgeService(db)  # type: ignore[arg-type]
    monkeypatch.setattr(svc, "_can_access", lambda d, u: True)
    monkeypatch.setattr("app.core.tasks.is_task_active", lambda name: False)

    with pytest.raises(ServiceError):
        await svc.delete_document(99, current_user=SimpleNamespace(id=1))  # type: ignore[arg-type]

    assert db.delete_called is False
    assert db.flush_called is False


@pytest.mark.asyncio
async def test_delete_document_blocks_when_background_task_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """processing_status 已落库为终态但后台任务仍活跃时也必须拦截。"""
    doc = SimpleNamespace(
        id=99,
        owner_id=1,
        processing_status="completed",
        file_path=None,
    )
    db = _DeleteGuardDb(doc)
    svc = KnowledgeService(db)  # type: ignore[arg-type]
    monkeypatch.setattr(svc, "_can_access", lambda d, u: True)
    monkeypatch.setattr("app.core.tasks.is_task_active", lambda name: True)

    with pytest.raises(ServiceError):
        await svc.delete_document(99, current_user=SimpleNamespace(id=1))  # type: ignore[arg-type]

    assert db.delete_called is False


@pytest.mark.asyncio
async def test_delete_document_allows_when_idle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无活跃任务且非处理中, 允许正常删除。"""
    doc = SimpleNamespace(
        id=99,
        owner_id=1,
        processing_status="completed",
        file_path=None,
    )
    db = _DeleteGuardDb(doc)
    svc = KnowledgeService(db)  # type: ignore[arg-type]
    monkeypatch.setattr(svc, "_can_access", lambda d, u: True)
    monkeypatch.setattr("app.core.tasks.is_task_active", lambda name: False)

    await svc.delete_document(99, current_user=SimpleNamespace(id=1))  # type: ignore[arg-type]

    assert db.delete_called is True
    assert db.flush_called is True


class _ProcessFakeDb:
    """模拟 ``execute_process_document`` 路径所需的 AsyncSession。

    - 第一次 ``get(KbDocument, id)`` 返回入口加载的 ``doc``。
    - 第二次 ``get(KbDocument, id, populate_existing=True)`` 返回 ``second_doc``,
      允许测试模拟"父文档在解析/向量化期间被并发删除"。
    - ``add`` / ``flush`` / ``execute`` 都不真正写 DB, 仅记录调用次数, 用于断言
      "缺失父行时不会继续写入"。
    """

    def __init__(self, doc: object, second_doc: object) -> None:
        self._doc = doc
        self._second_doc = second_doc
        self._get_calls = 0
        self.added: list[object] = []
        self.executed: list[object] = []
        self.flush_count = 0

    async def get(self, model: object, document_id: int, **kwargs: object) -> object:
        self._get_calls += 1
        return self._doc if self._get_calls == 1 else self._second_doc

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def execute(self, stmt: object) -> None:
        self.executed.append(stmt)

    async def flush(self) -> None:
        self.flush_count += 1

    async def refresh(self, obj: object) -> None:
        pass


@pytest.mark.asyncio
async def test_execute_process_document_raises_when_parent_deleted_mid_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """解析/向量化结束后父文档已被删除时, 必须抛出 ServiceError 而非撞外键。"""
    file_path = tmp_path / "kb.md"
    file_path.write_text("公司简介", encoding="utf-8")
    doc = SimpleNamespace(
        id=14,
        file_path=str(file_path),
        title="公司简介",
        business_type="company_profile",
        business_label=None,
        processing_status="saved",
        error_message=None,
    )
    db = _ProcessFakeDb(doc=doc, second_doc=None)
    svc = KnowledgeService(db)  # type: ignore[arg-type]

    class FakeAtomized:
        page_texts = [(1, "公司简介\n长期服务政企客户。")]
        page_count = 1
        full_text = "公司简介\n长期服务政企客户。"
        atoms: list[object] = []

    class FakeAtomizer:
        def __init__(self, enable_vl: bool = True) -> None:
            self.enable_vl = enable_vl

        async def atomize(self, **kwargs: object) -> FakeAtomized:
            return FakeAtomized()

    monkeypatch.setattr(
        "app.core.knowledge.atomizer.KnowledgeAtomizer",
        FakeAtomizer,
    )

    with pytest.raises(ServiceError) as exc:
        await svc.execute_process_document(14)

    assert "已被删除" in str(exc.value)
    chunk_inserts = [obj for obj in db.added if obj.__class__.__name__ == "KbChunk"]
    atom_inserts = [obj for obj in db.added if obj.__class__.__name__ == "KbAtom"]
    assert chunk_inserts == []
    assert atom_inserts == []
    assert doc.processing_status == "processing"


@pytest.mark.asyncio
async def test_execute_process_document_does_not_flush_failed_state_inline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """异常分支禁止在已失败事务里继续 flush('failed'), 交由任务层 rollback 回写。"""
    file_path = tmp_path / "empty.md"
    file_path.write_text("", encoding="utf-8")
    doc = SimpleNamespace(
        id=21,
        file_path=str(file_path),
        title="占位",
        business_type="other",
        business_label=None,
        processing_status="saved",
        error_message=None,
    )
    db = _ProcessFakeDb(doc=doc, second_doc=doc)
    svc = KnowledgeService(db)  # type: ignore[arg-type]

    class EmptyAtomized:
        page_texts: list[tuple[int, str]] = []
        page_count = 0
        full_text = "   "
        atoms: list[object] = []

    class FakeAtomizer:
        def __init__(self, enable_vl: bool = True) -> None:
            self.enable_vl = enable_vl

        async def atomize(self, **kwargs: object) -> EmptyAtomized:
            return EmptyAtomized()

    monkeypatch.setattr(
        "app.core.knowledge.atomizer.KnowledgeAtomizer",
        FakeAtomizer,
    )

    flush_count_before_raise = db.flush_count

    with pytest.raises(ServiceError):
        await svc.execute_process_document(21)

    assert doc.processing_status == "processing"
    assert doc.error_message is None
    assert db.flush_count == flush_count_before_raise + 1
