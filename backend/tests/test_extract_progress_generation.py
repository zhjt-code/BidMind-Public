# -*- coding: utf-8 -*-
"""提取进度代际条件写入防御测试。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.tasks.extract_progress_generation import (  # noqa: E402
    ExtractProgressGeneration,
    begin_extract_generation,
    bind_extract_generation,
    parse_progress_value,
    phase_rank_for_stage,
    reset_extract_generation_state_for_tests,
    should_preserve_extract_heartbeat,
    write_extract_heartbeat_if_current,
)


@pytest.fixture(autouse=True)
def _reset_state():
    reset_extract_generation_state_for_tests()
    _clear_test_extract_redis_keys()
    yield
    reset_extract_generation_state_for_tests()
    _clear_test_extract_redis_keys()


def _clear_test_extract_redis_keys() -> None:
    """清理本文件用例使用的提取心跳/代际 Redis key，避免真实 Redis 残留污染断言。"""
    try:
        from app.core.redis import get_sync_redis

        redis = get_sync_redis()
        if not redis:
            return
        task_names = (
            "extract_project_839",
            "extract_project_840",
            "extract_project_841",
            "extract_project_842",
            "extract_project_843",
            "extract_project_844",
            "extract_project_902",
            "extract_project_bug873_probe",
            "extract_project_bug937_probe",
            "extract_project_1012",
            "extract_project_1081",
            "extract_project_1081b",
        )
        keys: list[str] = []
        for name in task_names:
            keys.append(f"bidmind:heartbeat:{name}")
            keys.append(f"bidmind:extract-generation:{name}")
        if keys:
            redis.delete(*keys)
    except Exception:
        return


def test_phase_rank_queued_below_started_and_extract():
    assert phase_rank_for_stage("queued") < phase_rank_for_stage("启动")
    assert phase_rank_for_stage("启动") < phase_rank_for_stage("extracting")
    assert phase_rank_for_stage("extracting") < phase_rank_for_stage("mandatory_coverage")
    assert phase_rank_for_stage("mandatory_coverage") < phase_rank_for_stage("saving")
    assert phase_rank_for_stage("saving") < phase_rank_for_stage("scoring_extract")
    assert phase_rank_for_stage("scoring_extract") < phase_rank_for_stage("completed")


def test_same_step_progress_regress_rejected_but_next_step_advances():
    """: 子完成 saving@89 后, 同一步 merging@76 被拒; 独立评分步@90 放行。"""
    task_name = "extract_project_1012"
    begin_extract_generation(task_name)
    child_done = write_extract_heartbeat_if_current(
        task_name,
        stage="saving",
        progress="89%|提取完成，共 172 条",
    )
    assert child_done is not None and child_done.accepted

    regress = write_extract_heartbeat_if_current(
        task_name,
        stage="merging",
        progress="76%|合并去重",
    )
    assert regress is not None
    assert regress.accepted is False
    assert regress.reason == "progress_regress"

    advance = write_extract_heartbeat_if_current(
        task_name,
        stage="scoring_extract",
        progress="90%|评分项提取中",
    )
    assert advance is not None and advance.accepted


def test_monotonic_saving_sequence_accepted_after_merge() -> None:
    """: 合并 100% 接到 saving 0→10→85，同一步 overall 不回退，心跳全接受。"""
    task_name = "extract_project_1081"
    begin_extract_generation(task_name)
    reports = [
        ("merging", "76%|合并完成，10 条"),
        ("saving", "76%|合并去重后 10 条"),
        ("saving", "77%|评审意义判定"),
        ("saving", "87%|评审意义判定 10/10 批"),
        ("saving", "87%|保存到数据库"),
    ]
    outcome = None
    for stage, progress in reports:
        outcome = write_extract_heartbeat_if_current(
            task_name,
            stage=stage,
            progress=progress,
        )
        assert outcome is not None and outcome.accepted, progress
    assert outcome is not None
    assert outcome.snapshot.get("progress") == "87%|保存到数据库"


def test_observational_saving_bump_then_real_work_rejected() -> None:
    """同形态：观测占位抬高 saving overall 后，评审意义从更低 percent 起步被防回退丢掉。"""
    task_name = "extract_project_1081b"
    begin_extract_generation(task_name)
    bump = write_extract_heartbeat_if_current(
        task_name,
        stage="saving",
        progress="78%|输出精度闸门元信息",
    )
    assert bump is not None and bump.accepted

    real = write_extract_heartbeat_if_current(
        task_name,
        stage="saving",
        progress="77%|评审意义判定",
    )
    assert real is not None
    assert real.accepted is False
    assert real.reason == "progress_regress"


def test_parse_progress_value_supports_ratio_and_percent():
    assert parse_progress_value("16/62") == pytest.approx(16 / 62 * 100.0)
    assert parse_progress_value("45%|合规要求") == 45.0
    assert parse_progress_value("") == 0.0


def test_new_generation_rejects_old_generation_writes():
    task_name = "extract_project_839"
    gen1 = begin_extract_generation(task_name)
    first = write_extract_heartbeat_if_current(
        task_name,
        stage="queued",
        progress="",
        extra={"queue_mode": "arq"},
    )
    assert first is not None and first.accepted

    gen2 = begin_extract_generation(task_name)
    assert gen2.generation == gen1.generation + 1
    assert gen2.run_id != gen1.run_id

    second = write_extract_heartbeat_if_current(
        task_name,
        stage="extracting",
        progress="10%|x",
    )
    assert second is not None and second.accepted

    # 模拟旧 worker 仍绑定 gen1
    bind_extract_generation(task_name, gen1)
    rejected = write_extract_heartbeat_if_current(
        task_name,
        stage="completed",
        progress="100%|done",
    )
    assert rejected is not None
    assert rejected.accepted is False
    assert rejected.reason == "stale_generation"


def test_same_generation_rejects_progress_regression_but_accepts_advance():
    task_name = "extract_project_840"
    begin_extract_generation(task_name)
    mid = write_extract_heartbeat_if_current(
        task_name,
        stage="extracting",
        progress="40%|合规要求",
    )
    assert mid is not None and mid.accepted
    assert mid.revision == 1

    regress = write_extract_heartbeat_if_current(
        task_name,
        stage="extracting",
        progress="20%|合规要求",
    )
    assert regress is not None
    assert regress.accepted is False
    assert regress.reason == "progress_regress"

    advance = write_extract_heartbeat_if_current(
        task_name,
        stage="extracting",
        progress="55%|合规要求",
    )
    assert advance is not None and advance.accepted
    assert advance.revision == 2


def test_queued_cannot_regress_after_started_same_generation():
    task_name = "extract_project_841"
    begin_extract_generation(task_name)
    started = write_extract_heartbeat_if_current(
        task_name,
        stage="启动",
        progress="初始化中",
    )
    assert started is not None and started.accepted

    queued_late = write_extract_heartbeat_if_current(
        task_name,
        stage="queued",
        progress="",
        extra={"queue_mode": "arq"},
    )
    assert queued_late is not None
    assert queued_late.accepted is False
    assert queued_late.reason == "phase_regress"


def test_terminal_sticky_rejects_late_non_terminal():
    task_name = "extract_project_842"
    begin_extract_generation(task_name)
    done = write_extract_heartbeat_if_current(
        task_name,
        stage="completed",
        progress="100%|完成",
    )
    assert done is not None and done.accepted

    late = write_extract_heartbeat_if_current(
        task_name,
        stage="extracting",
        progress="90%|x",
    )
    assert late is not None
    assert late.accepted is False
    assert late.reason == "terminal_sticky"


def test_new_generation_overwrites_completed_heartbeat():
    """: 重新提取开新代际后，必须能写入非终态进度。"""
    task_name = "extract_project_902"
    gen1 = begin_extract_generation(task_name)
    done = write_extract_heartbeat_if_current(
        task_name,
        stage="completed",
        progress="100%|完成",
    )
    assert done is not None and done.accepted

    gen2 = begin_extract_generation(task_name)
    assert gen2.run_id != gen1.run_id
    started = write_extract_heartbeat_if_current(
        task_name,
        stage="extracting",
        progress="10%|开始提取",
    )
    assert started is not None
    assert started.accepted is True
    assert started.reason == "accepted"


def test_should_preserve_heartbeat_when_generation_advanced():
    task_name = "extract_project_843"
    owned = begin_extract_generation(task_name)
    newer = begin_extract_generation(task_name)
    assert should_preserve_extract_heartbeat(task_name, owned) is True
    assert should_preserve_extract_heartbeat(task_name, newer) is False


class _FakeRedis:
    """最小 Redis 替身：支持 hget/hset/hincrby/expire/eval。"""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.scripts: list[str] = []

    def hget(self, key: str, field: str) -> str | None:
        return self.hashes.get(key, {}).get(field)

    def hset(self, key: str, *args: Any, **kwargs: Any) -> int:
        bucket = self.hashes.setdefault(key, {})
        mapping = kwargs.get("mapping")
        if mapping is not None:
            for field, value in mapping.items():
                bucket[str(field)] = str(value)
            return len(mapping)
        if len(args) == 1 and isinstance(args[0], dict):
            for field, value in args[0].items():
                bucket[str(field)] = str(value)
            return len(args[0])
        if len(args) >= 2 and len(args) % 2 == 0:
            it = iter(args)
            count = 0
            for field in it:
                value = next(it)
                bucket[str(field)] = str(value)
                count += 1
            return count
        if len(args) == 2:
            bucket[str(args[0])] = str(args[1])
            return 1
        return 0

    def hincrby(self, key: str, field: str, amount: int = 1) -> int:
        bucket = self.hashes.setdefault(key, {})
        value = int(bucket.get(field) or 0) + int(amount)
        bucket[field] = str(value)
        return value

    def expire(self, key: str, ttl: int) -> bool:  # noqa: ARG002
        return True

    def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any:
        self.scripts.append(script)
        keys = list(keys_and_args[:numkeys])
        args = list(keys_and_args[numkeys:])
        # 简易实现 begin / write 两脚本语义
        if "HINCRBY" in script and "'generation', 1" in script:
            gen_key = keys[0]
            run_id = args[1]
            generation = self.hincrby(gen_key, "generation", 1)
            self.hset(gen_key, "run_id", run_id)
            if numkeys >= 2:
                self.hashes.pop(keys[1], None)
            return generation

        hb_key, gen_key = keys
        expect_run_id = args[0]
        expect_generation = args[1]
        stage = args[2]
        progress = args[3]
        phase_rank = float(args[4])
        progress_value = float(args[5])
        ts = args[6]
        is_terminal = args[7] == "1"
        import json

        payload = json.loads(args[8])
        cur_run = self.hget(gen_key, "run_id")
        cur_gen = self.hget(gen_key, "generation")
        if cur_run != expect_run_id or cur_gen != expect_generation:
            return [0, "stale_generation", "0"]
        hb_run = self.hget(hb_key, "run_id")
        if not hb_run or hb_run != expect_run_id:
            self.hashes.pop(hb_key, None)
        else:
            old_stage = self.hget(hb_key, "stage") or ""
            old_terminal = old_stage in {
                "completed",
                "failed",
                "失败",
                "cancelled",
                "canceled",
            }
            if old_terminal and not is_terminal:
                self.hset(hb_key, "last_heartbeat", ts)
                return [0, "terminal_sticky", self.hget(hb_key, "revision") or "0"]
            old_rank = float(self.hget(hb_key, "phase_rank") or -1)
            old_prog = float(self.hget(hb_key, "progress_value") or -1)
            if phase_rank < old_rank:
                self.hset(hb_key, "last_heartbeat", ts)
                return [0, "phase_regress", self.hget(hb_key, "revision") or "0"]
            if phase_rank == old_rank and progress_value < old_prog:
                self.hset(hb_key, "last_heartbeat", ts)
                return [0, "progress_regress", self.hget(hb_key, "revision") or "0"]
        revision = self.hincrby(hb_key, "revision", 1)
        mapping = {
            "last_heartbeat": ts,
            "stage": stage,
            "progress": progress,
            "run_id": expect_run_id,
            "generation": expect_generation,
            "phase_rank": phase_rank,
            "progress_value": progress_value,
            **payload,
        }
        self.hset(hb_key, mapping=mapping)
        return [1, "accepted", str(revision)]


def test_redis_lua_path_rejects_stale_and_accepts_current(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(
        "app.core.redis.get_sync_redis",
        lambda: fake,
    )
    task_name = "extract_project_844"
    gen1 = begin_extract_generation(task_name)
    ok = write_extract_heartbeat_if_current(
        task_name,
        stage="queued",
        progress="",
    )
    assert ok is not None and ok.accepted

    gen2 = begin_extract_generation(task_name)
    bind_extract_generation(task_name, gen2)
    ok2 = write_extract_heartbeat_if_current(
        task_name,
        stage="extracting",
        progress="12%|x",
    )
    assert ok2 is not None and ok2.accepted

    bind_extract_generation(task_name, gen1)
    stale = write_extract_heartbeat_if_current(
        task_name,
        stage="completed",
        progress="100%",
    )
    assert stale is not None
    assert stale.accepted is False
    assert fake.hashes[f"bidmind:heartbeat:{task_name}"]["stage"] == "extracting"


def test_redis_new_generation_overwrites_completed_heartbeat(monkeypatch):
    """: Redis 路径上 completed 后新代际不得被 terminal_sticky 粘死。"""
    fake = _FakeRedis()
    monkeypatch.setattr(
        "app.core.redis.get_sync_redis",
        lambda: fake,
    )
    task_name = "extract_project_902"
    gen1 = begin_extract_generation(task_name)
    done = write_extract_heartbeat_if_current(
        task_name,
        stage="completed",
        progress="100%|完成",
    )
    assert done is not None and done.accepted
    assert fake.hashes[f"bidmind:heartbeat:{task_name}"]["stage"] == "completed"
    assert fake.hashes[f"bidmind:heartbeat:{task_name}"]["run_id"] == gen1.run_id

    gen2 = begin_extract_generation(task_name)
    bind_extract_generation(task_name, gen2)
    started = write_extract_heartbeat_if_current(
        task_name,
        stage="extracting",
        progress="10%|开始提取",
    )
    assert started is not None
    assert started.accepted is True
    heartbeat = fake.hashes[f"bidmind:heartbeat:{task_name}"]
    assert heartbeat["stage"] == "extracting"
    assert heartbeat["run_id"] == gen2.run_id


def test_write_heartbeat_lua_uses_single_field_hset_only():
    """: Redis 3.0 拒绝多字段 HSET，脚本必须逐字段写入。"""
    import re

    from app.core.tasks import extract_progress_generation as mod

    script = mod._WRITE_HEARTBEAT_LUA
    for match in re.finditer(
        r"redis\.call\(\s*'HSET'\s*((?:,\s*[^,\)]+)+)\)",
        script,
    ):
        args = [part.strip() for part in match.group(1).split(",") if part.strip()]
        assert len(args) == 3, (
            "HSET must be single field/value on Redis 3.x; "
            f"found {len(args)} args in: {match.group(0)}"
        )


def test_write_heartbeat_lua_hset_values_are_redis_safe():
    """: redis.call 参数不得是 Lua bool/非整数 float（Redis 4+ 拒绝）。"""
    from app.core.tasks import extract_progress_generation as mod

    script = " ".join(mod._WRITE_HEARTBEAT_LUA.split())
    assert "HSET', hb_key, 'phase_rank', phase_rank)" not in script
    assert "HSET', hb_key, 'progress_value', progress_value)" not in script
    assert "HSET', hb_key, k, v)" not in script


def test_real_redis_heartbeat_write_persists_stage_and_run_id():
    """对真实 Redis 做一轮写入，证明 stage/run_id 能落盘（覆盖 3.0 arity）。"""
    try:
        from app.core.redis import get_sync_redis

        redis = get_sync_redis()
        if not redis:
            pytest.skip("Redis unavailable")
        redis.ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Redis unavailable: {exc}")

    task_name = "extract_project_bug873_probe"
    hb_key = f"bidmind:heartbeat:{task_name}"
    gen_key = f"bidmind:extract-generation:{task_name}"
    try:
        redis.delete(hb_key, gen_key)
        gen = begin_extract_generation(task_name)
        outcome = write_extract_heartbeat_if_current(
            task_name,
            stage="extracting",
            progress="15%|probe",
            extra={"probe": "bug873"},
        )
        assert outcome is not None
        assert outcome.accepted is True
        raw = redis.hgetall(hb_key)
        assert raw.get("stage") == "extracting"
        assert raw.get("run_id") == gen.run_id
        assert raw.get("progress") == "15%|probe"
        assert int(raw.get("revision") or 0) >= 1
        assert int(redis.ttl(hb_key) or 0) > 0
    finally:
        redis.delete(hb_key, gen_key)
        reset_extract_generation_state_for_tests()


def test_real_redis_heartbeat_write_persists_bool_and_float_extra():
    """: keepalive bool 与 enqueued_at 浮点必须落成 Redis 字符串，不能被 Lua 丢掉。"""
    try:
        from app.core.redis import get_sync_redis

        redis = get_sync_redis()
        if not redis:
            pytest.skip("Redis unavailable")
        redis.ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Redis unavailable: {exc}")

    task_name = "extract_project_bug937_probe"
    hb_key = f"bidmind:heartbeat:{task_name}"
    gen_key = f"bidmind:extract-generation:{task_name}"
    try:
        redis.delete(hb_key, gen_key)
        begin_extract_generation(task_name)
        outcome = write_extract_heartbeat_if_current(
            task_name,
            stage="scoring_extract",
            progress="85%|评分项提取中",
            extra={
                "keepalive": True,
                "enqueued_at": 1710000000.123,
                "queue_mode": "arq",
            },
        )
        assert outcome is not None
        assert outcome.accepted is True
        raw = redis.hgetall(hb_key)
        assert raw.get("stage") == "scoring_extract"
        assert raw.get("keepalive") in {"true", "True", "1"}
        assert raw.get("enqueued_at") is not None
        assert float(raw["enqueued_at"]) == pytest.approx(1710000000.123)
        assert raw.get("queue_mode") == "arq"
    finally:
        redis.delete(hb_key, gen_key)
        reset_extract_generation_state_for_tests()
