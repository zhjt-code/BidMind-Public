import pytest


class FakeRepository:
    def __init__(self, ready=None) -> None:
        self.ready = ready
        self.saved_failures = []
        self.saved_ready = []

    async def get_ready_by_fingerprint(self, outline_node_id: int, input_fingerprint: str):
        return self.ready

    async def save_failed_plan(self, request, failure_code: str, failure_reason: str):
        self.saved_failures.append((failure_code, failure_reason))
        return 42

    async def save_ready_plan(self, request, plan_payload: dict):
        self.saved_ready.append(plan_payload)
        return 43


class FakeResult:
    def __init__(self, scalar_value=0) -> None:
        self.scalar_value = scalar_value

    def scalar(self):
        return self.scalar_value

    def scalars(self):
        return self

    def first(self):
        return None


class FakeDb:
    def __init__(self) -> None:
        self.statements = []
        self.added = []

    async def execute(self, statement):
        self.statements.append(statement)
        return FakeResult(0)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass

    async def refresh(self, obj):
        obj.id = 55


class FakeClient:
    def __init__(self, result=None, exc: Exception | None = None) -> None:
        self.result = result
        self.exc = exc
        self.calls = 0

    async def chat_with_validation(self, **kwargs):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.result


@pytest.mark.asyncio
async def test_resolve_section_plan_reuses_ready_plan_without_llm_call() -> None:
    from app.core.writer.section_plan.contracts import SectionPlanRequest, SectionPlanResolution
    from app.core.writer.section_plan.service import resolve_section_plan

    request = SectionPlanRequest(
        project_id=1,
        outline_node_id=2,
        node_title="技术方案",
        target_word_count=1000,
        constraints=[],
        evidence_catalog=[],
        prompt_snapshot={"planner_prompt_sha256": "b" * 64, "source": "code"},
        input_fingerprint="c" * 64,
    )
    repo = FakeRepository(
        ready=SectionPlanResolution(
            mode="planned_reused",
            section_plan_id=7,
            plan={"section_goal": "复用"},
        )
    )
    client = FakeClient()

    result = await resolve_section_plan(request, client, repo)

    assert result.mode == "planned_reused"
    assert result.section_plan_id == 7
    assert client.calls == 0


@pytest.mark.asyncio
async def test_resolve_section_plan_falls_back_on_llm_failure() -> None:
    from app.core.writer.section_plan.contracts import SectionPlanRequest
    from app.core.writer.section_plan.service import resolve_section_plan

    request = SectionPlanRequest(
        project_id=1,
        outline_node_id=2,
        node_title="技术方案",
        target_word_count=1000,
        constraints=[],
        evidence_catalog=[],
        prompt_snapshot={"planner_prompt_sha256": "b" * 64, "source": "code"},
        input_fingerprint="c" * 64,
    )
    repo = FakeRepository()
    client = FakeClient(exc=TimeoutError("slow"))

    result = await resolve_section_plan(request, client, repo)

    assert result.mode == "legacy_fallback"
    assert result.failure_code == "planner_failed"
    assert repo.saved_failures[0][0] == "planner_failed"


@pytest.mark.asyncio
async def test_repository_save_ready_plan_clears_previous_current() -> None:
    from app.core.writer.section_plan.contracts import SectionPlanRequest
    from app.core.writer.section_plan.repository import SectionPlanRepository

    request = SectionPlanRequest(
        project_id=1,
        outline_node_id=2,
        node_title="技术方案",
        target_word_count=1000,
        constraints=[],
        evidence_catalog=[],
        prompt_snapshot={"planner_prompt_sha256": "b" * 64, "source": "code"},
        input_fingerprint="c" * 64,
    )
    db = FakeDb()

    plan_id = await SectionPlanRepository(db).save_ready_plan(request, {"section_goal": "目标"})

    assert plan_id == 55
    assert db.added[0].is_current == 1
    assert any("UPDATE writing_section_plans" in str(stmt) for stmt in db.statements)


@pytest.mark.asyncio
async def test_resolve_section_plan_returns_planned_sanitized_when_safety_patched() -> None:
    from app.core.writer.section_plan.contracts import SectionPlanRequest
    from app.core.writer.section_plan.service import resolve_section_plan

    request = SectionPlanRequest(
        project_id=1,
        outline_node_id=2,
        node_title="技术方案",
        target_word_count=1000,
        constraints=[],
        evidence_catalog=[],
        prompt_snapshot={"planner_prompt_sha256": "b" * 64, "source": "code"},
        input_fingerprint="c" * 64,
    )
    client = FakeClient(result={
        "section_goal": "形成可执行的技术方案",
        "arguments": [{"argument_id": "arg_1", "claim": "建立实施路径"}],
        "measures": [{
            "measure_id": "measure_1",
            "action": "承诺120天内完成测试",
            "steps": ["编制计划"],
            "verification": "按节点检查",
        }],
        "acceptance_criteria": [{
            "criterion_id": "acc_1",
            "criterion": "按计划完成",
            "verification_method": "检查记录",
        }],
    })
    repo = FakeRepository()

    result = await resolve_section_plan(request, client, repo)

    assert result.mode == "planned_sanitized"
    assert result.section_plan_id == 43
    assert repo.saved_ready[0]["safety_patches"]
    assert "[待补充:核验数值]" in repo.saved_ready[0]["measures"][0]["action"]
