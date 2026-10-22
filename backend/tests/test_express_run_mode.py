"""一步到位 run_mode / ExpressAdvance 形态夹具。

问题形态: 中间确认闸被跳过后，流水线必须按当前默认集合自动确认并续跑。
审标与写标是同一形态的两条夹具；guided 不得误开跑。
"""

from types import SimpleNamespace

import pytest

from app.core.exceptions import ServiceError
from app.models.project import ProjectStatus, ProjectType
from app.models.user import UserRole
from app.services.express_flow.advance import advance_express_project
from app.services.express_flow.dispatch import queue_express_advance_if_needed
from app.services.express_flow.review_express_service import ReviewExpressService
from app.services.express_flow.run_mode import (
    ProjectRunMode,
    is_express_run_mode,
    revert_run_mode_to_guided,
)
from app.services.express_flow.run_mode_service import RunModeService
from app.services.express_flow.writing_express_service import WritingExpressService


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _IdResult:
    def __init__(self, ids):
        self._ids = ids

    def all(self):
        return [(item,) for item in self._ids]


class _FakeDb:
    def __init__(self, project=None, ids=None):
        self.project = project
        self.ids = ids or []
        self.flush_count = 0
        self.statements = []
        self.refresh_names = None

    async def execute(self, statement):
        self.statements.append(statement)
        if self.project is not None:
            return _ScalarResult(self.project)
        return _IdResult(self.ids)

    async def flush(self):
        self.flush_count += 1

    async def refresh(self, _project, attribute_names=None):
        self.refresh_names = attribute_names
        return None


def _statement_loads_owner(statement) -> bool:
    return any("owner" in str(getattr(option, "path", "")) for option in statement._with_options)


def _user(user_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(id=user_id, username="owner", role=UserRole.USER)


def _project(**overrides) -> SimpleNamespace:
    data = dict(
        id=7,
        owner_id=1,
        owner=_user(),
        project_type=ProjectType.BID_REVIEW,
        status=ProjectStatus.CREATED,
        run_mode=ProjectRunMode.GUIDED.value,
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def _assert_set_run_mode_keeps_response_attrs_loaded(db: _FakeDb) -> None:
    """问题形态: 异步会话序列化时读取未加载或 flush 过期的属性会隐式 IO。"""
    assert db.statements
    assert _statement_loads_owner(db.statements[0])
    assert db.refresh_names == ["updated_at", "owner"]


@pytest.mark.asyncio
async def test_set_run_mode_allows_upload_phase() -> None:
    project = _project(status=ProjectStatus.PARSING)
    service = RunModeService(_FakeDb(project=project))

    updated = await service.set_run_mode(7, ProjectRunMode.EXPRESS, _user())

    assert updated.run_mode == "express"


@pytest.mark.asyncio
async def test_set_run_mode_eager_loads_owner_for_review_response() -> None:
    db = _FakeDb(project=_project(project_type=ProjectType.BID_REVIEW))
    await RunModeService(db).set_run_mode(7, ProjectRunMode.EXPRESS, _user())
    _assert_set_run_mode_keeps_response_attrs_loaded(db)


@pytest.mark.asyncio
async def test_set_run_mode_eager_loads_owner_for_writing_response() -> None:
    db = _FakeDb(project=_project(project_type=ProjectType.BID_WRITING))
    await RunModeService(db).set_run_mode(7, ProjectRunMode.EXPRESS, _user())
    _assert_set_run_mode_keeps_response_attrs_loaded(db)


@pytest.mark.asyncio
async def test_set_run_mode_rejects_after_extracting() -> None:
    project = _project(status=ProjectStatus.EXTRACTING, run_mode="guided")
    service = RunModeService(_FakeDb(project=project))

    with pytest.raises(ServiceError, match="提取开始后不能再更改运行模式"):
        await service.set_run_mode(7, ProjectRunMode.EXPRESS, _user())
    assert project.run_mode == "guided"


@pytest.mark.asyncio
async def test_set_run_mode_rejects_collusion() -> None:
    project = _project(project_type=ProjectType.COLLUSION_CHECK)
    service = RunModeService(_FakeDb(project=project))

    with pytest.raises(ServiceError, match="串标审查为固定一步流程"):
        await service.set_run_mode(7, ProjectRunMode.EXPRESS, _user())


@pytest.mark.asyncio
async def test_review_express_confirms_and_starts_review(monkeypatch) -> None:
    calls: list[str] = []

    class _Requirements:
        def __init__(self, _db):
            pass

        async def confirm_all(self, project_id, _user):
            calls.append(f"confirm:{project_id}")
            return 4

    class _Review:
        def __init__(self, _db):
            pass

        async def start_review(self, project_id, _user):
            calls.append(f"start:{project_id}")
            return {"status": "processing"}

    monkeypatch.setattr(
        "app.services.express_flow.review_express_service.RequirementService",
        _Requirements,
    )
    monkeypatch.setattr(
        "app.services.express_flow.review_express_service.ReviewService",
        _Review,
    )

    project = _project(status=ProjectStatus.EXTRACTED, run_mode="express")
    result = await ReviewExpressService(_FakeDb()).advance_express_review(project, _user())

    assert result["action"] == "started_review"
    assert calls == ["confirm:7", "start:7"]
    assert project.run_mode == "express"
    assert project.status == ProjectStatus.EXTRACTED


@pytest.mark.asyncio
async def test_review_guided_does_not_start_review(monkeypatch) -> None:
    called = []

    class _Boom:
        def __init__(self, _db):
            called.append("constructed")

    monkeypatch.setattr(
        "app.services.express_flow.review_express_service.RequirementService",
        _Boom,
    )
    project = _project(
        status=ProjectStatus.EXTRACTED,
        run_mode="guided",
        project_type=ProjectType.BID_REVIEW,
    )
    result = await advance_express_project(_FakeDb(project=project), 7)

    assert result == {"action": "skipped", "reason": "not_express"}
    assert called == []


@pytest.mark.asyncio
async def test_review_express_reverts_guided_when_no_confirmable_items(monkeypatch) -> None:
    class _Requirements:
        def __init__(self, _db):
            pass

        async def confirm_all(self, _project_id, _user):
            return 0

    class _Review:
        def __init__(self, _db):
            pass

        async def start_review(self, _project_id, _user):
            raise ServiceError("主审查列表为空（没有已确认的可审查要求）")

    monkeypatch.setattr(
        "app.services.express_flow.review_express_service.RequirementService",
        _Requirements,
    )
    monkeypatch.setattr(
        "app.services.express_flow.review_express_service.ReviewService",
        _Review,
    )

    project = _project(status=ProjectStatus.EXTRACTED, run_mode="express")
    db = _FakeDb()
    result = await ReviewExpressService(db).advance_express_review(project, _user())

    assert result["action"] == "reverted_guided"
    assert project.run_mode == "guided"
    assert project.status == ProjectStatus.EXTRACTED
    assert project.status != ProjectStatus.FAILED
    assert db.flush_count == 1


@pytest.mark.asyncio
async def test_writing_express_extracted_confirms_and_generates_outline(monkeypatch) -> None:
    calls: list[tuple] = []

    class _Writing:
        def __init__(self, _db):
            pass

        async def confirm_clauses(self, project_id, clause_ids, _user):
            calls.append(("confirm_clauses", project_id, tuple(clause_ids)))

        async def generate_outline(self, project_id, _user):
            calls.append(("generate_outline", project_id))

    monkeypatch.setattr(
        "app.services.express_flow.writing_express_service.WritingService",
        _Writing,
    )

    project = _project(
        status=ProjectStatus.EXTRACTED,
        run_mode="express",
        project_type=ProjectType.BID_WRITING,
    )
    result = await WritingExpressService(_FakeDb(ids=[11, 12])).advance_express_writing(
        project,
        _user(),
    )

    assert result == {"action": "started_outline", "confirmed_clauses": 2}
    assert calls == [("confirm_clauses", 7, (11, 12)), ("generate_outline", 7)]
    assert project.run_mode == "express"
    assert project.status == ProjectStatus.EXTRACTED


@pytest.mark.asyncio
async def test_writing_guided_stays_extracted(monkeypatch) -> None:
    constructed = []

    class _Writing:
        def __init__(self, _db):
            constructed.append("writing")

    monkeypatch.setattr(
        "app.services.express_flow.writing_express_service.WritingService",
        _Writing,
    )
    project = _project(
        status=ProjectStatus.EXTRACTED,
        run_mode="guided",
        project_type=ProjectType.BID_WRITING,
    )
    result = await advance_express_project(_FakeDb(project=project), 7)

    assert result == {"action": "skipped", "reason": "not_express"}
    assert constructed == []
    assert project.status == ProjectStatus.EXTRACTED


@pytest.mark.asyncio
async def test_writing_express_zero_confirmable_reverts_without_failed(monkeypatch) -> None:
    class _Writing:
        def __init__(self, _db):
            pass

        async def confirm_clauses(self, *_args, **_kwargs):
            raise AssertionError("0 条可确认项时不应调用 confirm_clauses")

        async def generate_outline(self, *_args, **_kwargs):
            raise ServiceError("没有可生成目录的已确认约束")

    monkeypatch.setattr(
        "app.services.express_flow.writing_express_service.WritingService",
        _Writing,
    )

    project = _project(
        status=ProjectStatus.EXTRACTED,
        run_mode="express",
        project_type=ProjectType.BID_WRITING,
    )
    result = await WritingExpressService(_FakeDb(ids=[])).advance_express_writing(
        project,
        _user(),
    )

    assert result["action"] == "reverted_guided"
    assert project.run_mode == "guided"
    assert project.status == ProjectStatus.EXTRACTED
    assert project.status != ProjectStatus.FAILED


@pytest.mark.asyncio
async def test_writing_express_outlined_triggers_match(monkeypatch) -> None:
    calls: list[str] = []

    class _Writing:
        def __init__(self, _db):
            pass

        async def trigger_match(self, project_id, _user):
            calls.append(f"match:{project_id}")

    monkeypatch.setattr(
        "app.services.express_flow.writing_express_service.WritingService",
        _Writing,
    )
    project = _project(status=ProjectStatus.OUTLINED, run_mode="express")
    result = await WritingExpressService(_FakeDb()).advance_express_writing(project, _user())

    assert result == {"action": "started_match"}
    assert calls == ["match:7"]


@pytest.mark.asyncio
async def test_writing_express_matched_confirms_bindings_and_composes(monkeypatch) -> None:
    calls: list[tuple] = []

    class _Writing:
        def __init__(self, _db):
            pass

        async def confirm_bindings(self, project_id, binding_ids, _user):
            calls.append(("confirm_bindings", project_id, tuple(binding_ids)))

        async def trigger_compose(self, project_id, _user):
            calls.append(("trigger_compose", project_id))

    monkeypatch.setattr(
        "app.services.express_flow.writing_express_service.WritingService",
        _Writing,
    )
    project = _project(status=ProjectStatus.MATCHED, run_mode="express")
    result = await WritingExpressService(_FakeDb(ids=[21])).advance_express_writing(
        project,
        _user(),
    )

    assert result == {"action": "started_compose", "confirmed_bindings": 1}
    assert calls == [("confirm_bindings", 7, (21,)), ("trigger_compose", 7)]


@pytest.mark.asyncio
async def test_queue_express_advance_only_for_express(monkeypatch) -> None:
    queued: list[int] = []

    async def _dispatch(project_id: int) -> None:
        queued.append(project_id)

    monkeypatch.setattr(
        "app.services.express_flow.dispatch.dispatch_express_advance",
        _dispatch,
    )

    await queue_express_advance_if_needed("guided", 3)
    await queue_express_advance_if_needed(None, 4)
    await queue_express_advance_if_needed("express", 9)

    assert queued == [9]


def test_revert_run_mode_does_not_change_business_status() -> None:
    project = _project(status=ProjectStatus.EXTRACTED, run_mode="express")
    revert_run_mode_to_guided(project)
    assert project.run_mode == "guided"
    assert project.status == ProjectStatus.EXTRACTED
    assert is_express_run_mode("express") is True
    assert is_express_run_mode("guided") is False
