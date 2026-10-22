"""并行单元在途进度折算。"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.core.extractor.bid_extractor import BidExtractor
from app.core.tasks.parallel_unit_progress import (
    invoke_unit_progress,
    merge_running_step_detail,
    parallel_unit_heartbeat_extra,
    parallel_unit_progress_pct,
    parse_heartbeat_unit_counts,
    running_step_progress_from_heartbeat,
    stale_stage_scoped_keys,
)


def test_inflight_weight_moves_bar_before_first_completion() -> None:
    assert parallel_unit_progress_pct(done=0, total=10, inflight=8) == 20


def test_completed_units_without_inflight_use_done_ratio() -> None:
    """同形态第二例：无在途时按已完成单元比例，不依赖提取实体。"""
    assert parallel_unit_progress_pct(done=2, total=10, inflight=0) == 20


def test_done_equals_total_is_full() -> None:
    assert parallel_unit_progress_pct(done=10, total=10, inflight=0) == 100


def test_incomplete_never_reports_100() -> None:
    assert parallel_unit_progress_pct(done=9, total=10, inflight=4) == 99


def test_heartbeat_extra_carries_weighted_pct() -> None:
    extra = parallel_unit_heartbeat_extra(done=0, total=10, inflight=8)
    assert extra["current"] == 0
    assert extra["total"] == 10
    assert extra["inflight"] == 8
    assert extra["stage_progress_pct"] == 20


def test_running_step_takes_max_of_persisted_and_heartbeat() -> None:
    extract_hb = {"stage": "步骤2-提取", "stage_progress_pct": 25}
    tokens = ("步骤2", "提取")
    assert running_step_progress_from_heartbeat(0, extract_hb, stage_tokens=tokens) == 25
    assert running_step_progress_from_heartbeat(40, extract_hb, stage_tokens=tokens) == 40


def test_heartbeat_100_does_not_complete_when_persisted_lag() -> None:
    assert running_step_progress_from_heartbeat(
        0,
        {"stage": "步骤2-提取", "stage_progress_pct": 100},
        stage_tokens=("步骤2", "提取"),
    ) == 99


def test_running_step_ignores_pct_from_other_stage() -> None:
    """: 装配满格百分数不得抬升审查步条宽。"""
    leftover = {
        "stage": "步骤3-匹配",
        "progress": "42/42",
        "stage_progress_pct": 100,
        "substage": "assemble_pairs",
    }
    assert running_step_progress_from_heartbeat(
        6,
        leftover,
        stage_tokens=("步骤4", "审查", "重试", "收尾"),
    ) == 6


def test_assemble_step_ignores_extract_full_fraction() -> None:
    """ 同形态：提取满格分数不得抬升装配步条宽。"""
    leftover = {
        "stage": "步骤2-提取",
        "progress": "42/42",
        "stage_progress_pct": 100,
        "current": 42,
        "total": 42,
    }
    assert running_step_progress_from_heartbeat(
        0,
        leftover,
        stage_tokens=("步骤3", "匹配", "装配", "assemble"),
    ) == 0


def test_running_step_ignores_leftover_substage_even_if_stage_already_moved() -> None:
    """同形态第二例：stage 已写成审查，但 substage/pct 仍属装配。"""
    leftover = {
        "stage": "步骤4-审查",
        "progress": "3/42",
        "stage_progress_pct": 100,
        "substage": "assemble_pairs",
    }
    assert running_step_progress_from_heartbeat(
        6,
        leftover,
        stage_tokens=("步骤4", "审查", "重试", "收尾"),
    ) == 6


def test_stale_stage_scoped_keys_drop_unspecified_progress_fields() -> None:
    dropped = stale_stage_scoped_keys({"phase", "keepalive"})
    assert "stage_progress_pct" in dropped
    assert "substage" in dropped
    assert "phase" not in dropped


def test_same_stage_keepalive_does_not_drop_scoped_keys() -> None:
    from app.core.tasks.parallel_unit_progress import redis_stage_scoped_fields_to_drop

    assert redis_stage_scoped_fields_to_drop(
        "步骤2-提取",
        "步骤2-提取",
        {"keepalive"},
    ) == ()


def test_stage_change_drops_unspecified_pct() -> None:
    from app.core.tasks.parallel_unit_progress import redis_stage_scoped_fields_to_drop

    dropped = redis_stage_scoped_fields_to_drop(
        "步骤3-匹配",
        "步骤4-审查",
        {"phase"},
    )
    assert "stage_progress_pct" in dropped
    assert "substage" in dropped
    assert "phase" not in dropped


def test_extract_step_ignores_assemble_pct() -> None:
    leftover = {
        "stage": "步骤3-匹配",
        "stage_progress_pct": 100,
        "substage": "assemble_pairs",
    }
    assert running_step_progress_from_heartbeat(
        0,
        leftover,
        stage_tokens=("步骤2", "提取"),
    ) == 0


def test_assemble_substage_still_lifts_assemble_step() -> None:
    assert running_step_progress_from_heartbeat(
        0,
        {
            "stage": "步骤3-匹配",
            "substage": "assemble_pairs",
            "stage_progress_pct": 80,
        },
        stage_tokens=("步骤3", "匹配", "装配", "assemble"),
    ) == 80


def test_parse_heartbeat_unit_counts_prefers_current_fields() -> None:
    done, total, inflight = parse_heartbeat_unit_counts(
        {"progress": "0/43", "current": 0, "total": 43, "inflight": 12}
    )
    assert (done, total, inflight) == (0, 43, 12)


def test_merge_running_step_detail_keeps_higher_persisted_count() -> None:
    """: 落库 27 时禁止被心跳 0/43 盖回开工分数。"""
    detail = merge_running_step_detail(
        27,
        43,
        {"progress": "0/43", "current": 0, "total": 43, "inflight": 10},
    )
    assert detail == "27/43"


def test_merge_running_step_detail_shows_inflight_when_zero_done() -> None:
    """同形态第二例：提取落库仍为 0 但在途>0 时文案必须带在途量。"""
    detail = merge_running_step_detail(
        0,
        10,
        {"progress": "0/10", "current": 0, "total": 10, "inflight": 8},
    )
    assert detail == "0/10 · 在途 8"


def test_merge_running_step_detail_ignores_other_step_counts() -> None:
    """装配残留 current=10 不得盖过审查落库 3/10。"""
    detail = merge_running_step_detail(
        3,
        10,
        {
            "stage": "步骤4-审查",
            "progress": "3/10",
            "current": 10,
            "total": 10,
            "substage": "assemble_pairs",
        },
        stage_tokens=("步骤4", "审查", "重试", "收尾"),
    )
    assert detail == "3/10"


def test_invoke_unit_progress_passes_inflight_and_falls_back() -> None:
    three: list[tuple[int, int, int]] = []
    two: list[tuple[int, int]] = []
    invoke_unit_progress(lambda d, t, inf=0: three.append((d, t, inf)), 1, 10, 3)
    invoke_unit_progress(lambda d, t: two.append((d, t)), 1, 10, 3)
    assert three == [(1, 10, 3)]
    assert two == [(1, 10)]


async def test_extract_for_project_emits_inflight_before_first_completion() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[tuple[int, int, int]] = []

    extractor = BidExtractor(db=MagicMock(), llm_client=object(), concurrency=3)
    reqs = [SimpleNamespace(id=i) for i in range(4)]

    async def fake_confirmed(_pid):
        return reqs

    async def fake_envelope(_pid):
        from app.core.bid_envelope import BidEnvelope

        return BidEnvelope(
            documents=(SimpleNamespace(id=99, bid_part=None, parsed=1),)
        )

    async def fake_extract(**_kwargs):
        started.set()
        await release.wait()
        return SimpleNamespace(requirement_id=1)

    extractor._get_confirmed_requirements = fake_confirmed  # type: ignore[method-assign]
    extractor._load_bid_envelope = fake_envelope  # type: ignore[method-assign]
    extractor._extract_single_response_decoupled = fake_extract  # type: ignore[method-assign]

    def cb(done: int, total: int, inflight: int = 0) -> None:
        calls.append((done, total, inflight))

    task = asyncio.create_task(
        extractor.extract_for_project(
            project_id=1,
            save_to_db=False,
            skip_existing=False,
            heartbeat_callback=cb,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=2)
    await asyncio.sleep(0)
    inflight_before_done = [c for c in calls if c[0] == 0 and c[2] > 0]
    release.set()
    await task

    assert inflight_before_done, calls
    assert inflight_before_done[0][1] == 4
    assert calls[-1] == (4, 4, 0)
    assert _done_when_inflight_first_drops(calls) >= 1, calls


async def test_review_project_emits_inflight_before_first_completion(monkeypatch) -> None:
    """同形态第二例：审查并行单元在首条落库前必须上报 inflight。"""
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[tuple[int, int, int]] = []

    from app.core.ai.reviewer import ReviewOrchestrator
    from app.core.ai.reviewer_parts.evidence_ledger.contracts import EvidenceLedger
    from app.core.matcher.requirement_matcher import MatchedPair

    orch = ReviewOrchestrator(db=None, llm_client=object(), project_id=1)
    pairs = [
        MatchedPair(
            requirement=SimpleNamespace(id=i, category=None, is_mandatory=False),
            response=SimpleNamespace(id=i, content="ok"),
        )
        for i in range(4)
    ]

    async def fake_prefetch(*_a, **_k):
        started.set()
        await release.wait()
        return None

    async def fake_review(*_a, **_k):
        return SimpleNamespace(requirement_id=1, comment="ok")

    orch._prefetch_evidence = fake_prefetch  # type: ignore[method-assign]
    orch._review_single_pair = fake_review  # type: ignore[method-assign]
    orch._build_evidence_index = lambda _pairs: {}  # type: ignore[method-assign]

    @asynccontextmanager
    async def fake_scope():
        yield MagicMock()

    async def fake_ledger(*_a, **_k):
        return EvidenceLedger()

    monkeypatch.setattr("app.core.database.db_session_scope", fake_scope)
    monkeypatch.setattr(
        "app.core.ai.reviewer_parts.evidence_ledger.builder.build_project_evidence_ledger",
        fake_ledger,
    )
    monkeypatch.setattr("app.core.config.get_review_concurrency", lambda: 3)

    def cb(done: int, total: int, inflight: int = 0, **_k) -> None:
        calls.append((done, total, inflight))

    task = asyncio.create_task(
        orch.review_project(
            project_id=1,
            matched_pairs=pairs,
            save_to_db=False,
            skip_existing=False,
            heartbeat_callback=cb,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=2)
    await asyncio.sleep(0)
    inflight_before_done = [c for c in calls if c[0] == 0 and c[2] > 0]
    release.set()
    await task

    assert inflight_before_done, calls
    assert inflight_before_done[0][1] == 4
    assert calls[-1] == (4, 4, 0)
    assert _done_when_inflight_first_drops(calls) >= 1, calls


def _done_when_inflight_first_drops(calls: list[tuple[int, int, int]]) -> int:
    """首个在途回落对应已完成数；落库当场计数时此处应 >= 1。"""
    peak = 0
    for done, _total, inflight in calls:
        if inflight < peak:
            return done
        peak = max(peak, inflight)
    raise AssertionError(f"inflight never dropped: {calls}")


class _FakeHashRedis:
    """同步心跳写入用的 Hash 替身：pipeline 命令立即落到 self.hash。"""

    def __init__(self) -> None:
        self.hash: dict[str, str] = {}

    def hget(self, _key: str, field: str) -> str | None:
        return self.hash.get(field)

    def pipeline(self) -> "_FakeHashRedis":
        return self

    def hset(self, _key: str, field: str, value: str) -> "_FakeHashRedis":
        self.hash[field] = value
        return self

    def hdel(self, _key: str, field: str) -> "_FakeHashRedis":
        self.hash.pop(field, None)
        return self

    def expire(self, *_args: object, **_kwargs: object) -> "_FakeHashRedis":
        return self

    def execute(self) -> list[object]:
        return []

    def delete(self, *_args: object, **_kwargs: object) -> int:
        self.hash.clear()
        return 1


def test_update_heartbeat_drops_assemble_pct_when_review_stage_starts(
    monkeypatch,
) -> None:
    from app.core.tasks import cleanup_task, update_heartbeat

    fake = _FakeHashRedis()
    monkeypatch.setattr("app.core.redis.get_sync_redis", lambda: fake)
    name = "review_project_heartbeat_1033"
    try:
        update_heartbeat(
            name,
            stage="步骤3-匹配",
            progress="42/42",
            extra={"stage_progress_pct": 100, "substage": "assemble_pairs"},
        )
        update_heartbeat(name, stage="步骤4-审查", progress="3/42")
        assert "stage_progress_pct" not in fake.hash
        assert "substage" not in fake.hash
        assert fake.hash["stage"] == "步骤4-审查"
        assert fake.hash["progress"] == "3/42"
    finally:
        cleanup_task(name)


def test_keepalive_same_stage_keeps_inflight_pct(monkeypatch) -> None:
    from app.core.tasks import cleanup_task, update_heartbeat

    fake = _FakeHashRedis()
    monkeypatch.setattr("app.core.redis.get_sync_redis", lambda: fake)
    name = "extract_project_heartbeat_1033"
    try:
        update_heartbeat(
            name,
            stage="步骤2-提取",
            progress="0/10",
            extra={
                "stage_progress_pct": 20,
                "inflight": 8,
                "current": 0,
                "total": 10,
            },
        )
        update_heartbeat(
            name,
            stage="步骤2-提取",
            progress="0/10",
            extra={"keepalive": True},
        )
        assert fake.hash["stage_progress_pct"] == "20"
        assert fake.hash["inflight"] == "8"
        assert fake.hash["keepalive"] == "true"
    finally:
        cleanup_task(name)


def test_queued_heartbeat_drops_previous_round_phase(monkeypatch) -> None:
    from app.core.tasks import cleanup_task, mark_task_queued, update_heartbeat

    fake = _FakeHashRedis()
    monkeypatch.setattr("app.core.redis.get_sync_redis", lambda: fake)
    name = "review_project_queued_1033"
    try:
        update_heartbeat(
            name,
            stage="步骤4-收尾",
            progress="42/42",
            extra={"phase": "finalizing", "stage_progress_pct": 100},
        )
        mark_task_queued(name, queue_mode="arq", project_id=1033)
        assert "phase" not in fake.hash
        assert "stage_progress_pct" not in fake.hash
        assert fake.hash["stage"] == "queued"
    finally:
        cleanup_task(name)


def test_empty_memory_drops_redis_leftover_on_stage_change(monkeypatch) -> None:
    import app.core.tasks as tasks_mod
    from app.core.tasks import cleanup_task, update_heartbeat

    fake = _FakeHashRedis()
    fake.hash = {
        "stage": "步骤3-匹配",
        "stage_progress_pct": "100",
        "substage": "assemble_pairs",
        "progress": "42/42",
    }
    monkeypatch.setattr("app.core.redis.get_sync_redis", lambda: fake)
    name = "review_project_restart_1033"
    try:
        tasks_mod._task_heartbeats.pop(name, None)
        update_heartbeat(name, stage="步骤4-审查", progress="0/42")
        assert "stage_progress_pct" not in fake.hash
        assert "substage" not in fake.hash
        assert fake.hash["stage"] == "步骤4-审查"
    finally:
        cleanup_task(name)


def test_assemble_start_overwrites_extract_full_pct(monkeypatch) -> None:
    """: 装配开工必须写回 0 extra，覆盖提取满格百分数。"""
    from app.core.tasks import cleanup_task, update_heartbeat
    from app.core.tasks.parallel_unit_progress import parallel_unit_heartbeat_extra

    fake = _FakeHashRedis()
    monkeypatch.setattr("app.core.redis.get_sync_redis", lambda: fake)
    name = "review_project_heartbeat_1111"
    try:
        update_heartbeat(
            name,
            stage="步骤2-提取",
            progress="42/42",
            extra=parallel_unit_heartbeat_extra(42, 42, 0),
        )
        update_heartbeat(
            name,
            stage="步骤3",
            progress="匹配中",
            extra=parallel_unit_heartbeat_extra(0, 0, 0),
        )
        assert fake.hash["stage"] == "步骤3"
        assert fake.hash["stage_progress_pct"] in {"0", 0}
        assert fake.hash["current"] in {"0", 0}
        assert fake.hash["progress"] == "匹配中"
    finally:
        cleanup_task(name)


async def test_match_project_emits_zero_before_pairing() -> None:
    """装配在拉响应前先报 0/N，避免查库窗口沿用上一步满格。"""
    from app.core.matcher.requirement_matcher import RequirementMatcher

    calls: list[tuple[int, int, str]] = []
    matcher = RequirementMatcher(object())
    req = SimpleNamespace(id=1, category=None, is_mandatory=False)

    async def fake_reqs(_pid: int):
        return [req]

    async def fake_resps(_pid: int):
        return []

    matcher._get_confirmed_requirements = fake_reqs  # type: ignore[method-assign]
    matcher._get_bid_responses = fake_resps  # type: ignore[method-assign]

    def cb(done: int, total: int, message: str) -> None:
        calls.append((done, total, message))

    pairs = await matcher.match_project(1, progress_callback=cb)
    assert calls[0][:2] == (0, 1)
    assert "0/1" in calls[0][2]
    assert calls[-1][:2] == (1, 1)
    assert len(pairs) == 1
