"""复核交给 LLM worker: 请求一次并发发出、单批失败只保留该批、池空跳过、只在 LLM 侧注册。

夹具含两种检查器实体(编号 / 语言), 并发与失败隔离不按规则码分支。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, Optional

import pytest

from app.core.config import settings
from app.core.document_qa.contracts import (
    Evidence,
    EvidenceItem,
    Finding,
    QaCategory,
    QaCertainty,
    QaSeverity,
)
from app.core.document_qa.verify.contracts import CandidateVerdict, PageVerifyResult
from app.core.document_qa.verify.finding_verifier import verify_findings
from app.core.tasks import document_qa_verify_task as verify_task
from tests.document_qa.conftest import make_model


def _finding(rule_code: str, page: int, excerpt: str) -> Finding:
    category = QaCategory.NUMBERING if "NUM" in rule_code else QaCategory.LANGUAGE
    return Finding(
        rule_code=rule_code,
        category=category,
        severity=QaSeverity.P2,
        certainty=QaCertainty.PROBABLE,
        title=rule_code,
        message=excerpt,
        evidence=Evidence(kind="single", items=(EvidenceItem(excerpt=excerpt, page=page),)),
        suggestion="核对原文",
        anchor=f"{rule_code}:{page}",
    )


def _verdicts_for(user_message: str, *, real: bool, confidence: float) -> dict[str, Any]:
    payload = json.loads(user_message[user_message.index("{"):])
    return {
        "verdicts": [
            {"candidate_id": item["candidate_id"], "is_real_defect": real, "confidence": confidence}
            for item in payload["candidates"]
        ],
    }


class _BarrierClient:
    """全部请求都进入后才一起返回; 串行调用会卡在第一条。"""

    def __init__(self, parties: int) -> None:
        self.parties = parties
        self.entered = 0
        self.all_in = asyncio.Event()

    async def chat_json(self, user_message: str, **_kwargs: Any) -> dict[str, Any]:
        self.entered += 1
        if self.entered >= self.parties:
            self.all_in.set()
        await self.all_in.wait()
        return _verdicts_for(user_message, real=True, confidence=0.95)


class _FakeRedis:
    def __init__(self, preset: Optional[dict[str, str]] = None) -> None:
        self.store: dict[str, str] = dict(preset or {})

    async def setex(self, key: str, _ttl: int, data: str) -> None:
        self.store[key] = data

    async def get(self, key: str) -> Optional[str]:
        return self.store.get(key)


def _model_and_findings() -> tuple[Any, list[Finding]]:
    model = make_model(body_texts={
        4: "括号并无未闭合情形。",
        9: "1.2 之后直接是 1.1, 编号顺序倒置。",
        12: "括号全部成对出现。",
    })
    findings = [
        _finding("DOC-LANG-001", 4, "括号并无未闭合"),
        _finding("DOC-NUM-002", 9, "编号顺序倒置"),
        _finding("DOC-LANG-001", 12, "括号全部成对"),
    ]
    return model, findings


# ── 复核取舍: 并发、失败隔离、池空 ─────────────────────────────────────


async def test_requests_are_sent_concurrently() -> None:
    model, findings = _model_and_findings()
    client = _BarrierClient(parties=3)

    kept, stats = await asyncio.wait_for(
        verify_findings(model, findings, project_id=1, client=client), timeout=2,
    )

    assert client.entered == 3
    assert stats["calls"] == 3
    assert len(kept) == 3


async def test_failed_request_keeps_only_its_batch() -> None:
    model, findings = _model_and_findings()

    async def ask(messages: list[str]) -> list[Any]:
        assert len(messages) == 3
        return [
            PageVerifyResult(verdicts=[
                CandidateVerdict(candidate_id="p4-0", is_real_defect=False, confidence=0.97),
            ]),
            RuntimeError("llm worker down"),
            PageVerifyResult(verdicts=[
                CandidateVerdict(candidate_id="p12-2", is_real_defect=True, confidence=0.9),
            ]),
        ]

    kept, stats = await verify_findings(model, findings, project_id=1, ask_requests=ask)

    assert [(item.rule_code, item.evidence.items[0].page) for item in kept] == [
        ("DOC-NUM-002", 9),
        ("DOC-LANG-001", 12),
    ]
    assert kept[0].certainty == QaCertainty.PROBABLE
    assert stats["dropped"] == 1
    assert stats["failed_calls"] == 1
    assert stats["degraded"] is True


async def test_no_extract_pool_skips_without_degrading() -> None:
    model, findings = _model_and_findings()

    async def ask(_messages: list[str]) -> None:
        return None

    kept, stats = await verify_findings(model, findings, project_id=1, ask_requests=ask)

    assert kept == findings
    assert stats == {"status": "skipped_no_extract_pool", "degraded": False}


# ── LLM worker 侧: 同序写回, 单条失败只记在该位 ─────────────────────────


async def test_llm_worker_batch_writes_aligned_results(monkeypatch) -> None:
    class _HalfBrokenClient:
        async def chat_json(self, user_message: str, **_kwargs: Any) -> dict[str, Any]:
            if "p9-1" in user_message:
                raise RuntimeError("upstream 502")
            return _verdicts_for(user_message, real=False, confidence=0.99)

    monkeypatch.setattr("app.core.ai.ai_factory.get_judge_client", lambda _pid: _HalfBrokenClient())
    redis = _FakeRedis()
    messages = [
        json.dumps({"page": 4, "candidates": [{"candidate_id": "p4-0"}]}),
        json.dumps({"page": 9, "candidates": [{"candidate_id": "p9-1"}]}),
    ]

    payload = await verify_task.run_document_qa_verify_batch({"redis": redis}, 7, "b1", messages)

    assert payload["status"] == "completed"
    assert payload["results"][0]["ok"] is True
    assert payload["results"][0]["verdicts"][0]["candidate_id"] == "p4-0"
    assert payload["results"][1]["ok"] is False
    assert "upstream 502" in payload["results"][1]["error"]
    assert json.loads(redis.store[verify_task._result_key("b1")]) == payload


async def test_verify_job_writes_cancelled_for_deleted_project(monkeypatch) -> None:
    async def cancelled(_redis, _project_id):
        return True

    monkeypatch.setattr("app.core.arq_runtime.is_project_cancelled_async", cancelled)
    redis = _FakeRedis()

    await verify_task.document_qa_verify_job({"redis": redis}, 7, "b2", ["{}"])

    assert json.loads(redis.store[verify_task._result_key("b2")])["status"] == "cancelled"


# ── 处理侧: 投递、等待、解码 ───────────────────────────────────────────


def _install_dispatch(monkeypatch, *, queued: bool, redis: Optional[_FakeRedis] = None) -> list[str]:
    batch_ids: list[str] = []

    async def enqueue(function_name, project_id, batch_id, messages):
        assert function_name == verify_task.DOCUMENT_QA_VERIFY_JOB
        batch_ids.append(batch_id)
        if redis is not None and "__result__" in redis.store:
            redis.store[verify_task._result_key(batch_id)] = redis.store.pop("__result__")
        return SimpleNamespace(queued=queued, reason="queued" if queued else "arq_unavailable")

    async def pool():
        return redis

    monkeypatch.setattr("app.core.arq_runtime.enqueue_job_detailed", enqueue)
    monkeypatch.setattr("app.core.redis.get_redis_pool", pool)
    monkeypatch.setattr(verify_task, "_POLL_INTERVAL_SEC", 0.01)
    return batch_ids


async def test_not_queued_fails_whole_batch(monkeypatch) -> None:
    _install_dispatch(monkeypatch, queued=False)

    outcomes = await verify_task.request_verdicts_from_llm_worker(7, ["{}", "{}"])

    assert len(outcomes) == 2
    assert all(isinstance(item, RuntimeError) for item in outcomes)


async def test_roundtrip_decodes_llm_worker_result(monkeypatch) -> None:
    result = {
        "status": "completed",
        "results": [
            {"ok": True, "verdicts": [
                {"candidate_id": "p4-0", "is_real_defect": False, "confidence": 0.97, "reason": ""},
            ]},
            {"ok": False, "error": "RuntimeError: upstream 502"},
        ],
    }
    redis = _FakeRedis({"__result__": json.dumps(result)})
    _install_dispatch(monkeypatch, queued=True, redis=redis)

    outcomes = await verify_task.request_verdicts_from_llm_worker(7, ["{}", "{}"])

    assert isinstance(outcomes[0], PageVerifyResult)
    assert outcomes[0].verdicts[0].candidate_id == "p4-0"
    assert isinstance(outcomes[1], RuntimeError)


async def test_skipped_pool_returns_none(monkeypatch) -> None:
    redis = _FakeRedis({"__result__": json.dumps({"status": "skipped_no_extract_pool", "results": []})})
    _install_dispatch(monkeypatch, queued=True, redis=redis)

    assert await verify_task.request_verdicts_from_llm_worker(7, ["{}"]) is None


async def test_wait_timeout_fails_whole_batch(monkeypatch) -> None:
    _install_dispatch(monkeypatch, queued=True, redis=_FakeRedis())
    monkeypatch.setattr(settings, "DOCUMENT_QA_VERIFY_WAIT_TIMEOUT", 0.05)

    outcomes = await verify_task.request_verdicts_from_llm_worker(7, ["{}", "{}"])

    assert [str(item) for item in outcomes] == ["wait_timeout", "wait_timeout"]


# ── 分工: 复核子任务只由 LLM worker 消费 ─────────────────────────────────


def _names(functions: list[Any]) -> set[str]:
    return {getattr(fn, "name", None) or getattr(fn, "__name__", "") for fn in functions}


def test_verify_job_registered_only_on_llm_worker() -> None:
    from app.core.tasks.resource_policy import resource_type_for_function
    from app.worker import LlmWorkerSettings, ProcessWorkerSettings

    assert verify_task.DOCUMENT_QA_VERIFY_JOB in _names(LlmWorkerSettings.functions)
    assert verify_task.DOCUMENT_QA_VERIFY_JOB not in _names(ProcessWorkerSettings.functions)
    assert resource_type_for_function(verify_task.DOCUMENT_QA_VERIFY_JOB) == "llm"


@pytest.fixture(autouse=True)
def _verify_enabled(monkeypatch):
    monkeypatch.setattr(settings, "DOCUMENT_QA_VERIFY_ENABLED", True)
