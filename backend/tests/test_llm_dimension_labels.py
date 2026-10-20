"""维度标签三态 / 覆盖率 / 失败下钻防御测试。"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.core.config import settings
from app.services.ops_metrics.llm_dimension_labels import (
    LABEL_STATE_LABELED,
    LABEL_STATE_NOT_COLLECTED,
    LABEL_STATE_UNRECOGNIZED,
    classify_raw_key,
)

_TC = "regression"


def _sync_engine():
    return create_engine(settings.DATABASE_URL.replace("+asyncpg", "+psycopg2"))


def test_classify_raw_key_three_states() -> None:
    assert classify_raw_key(None) == LABEL_STATE_NOT_COLLECTED
    assert classify_raw_key("") == LABEL_STATE_NOT_COLLECTED
    assert classify_raw_key("unknown") == LABEL_STATE_UNRECOGNIZED
    assert classify_raw_key("deepseek") == LABEL_STATE_LABELED


@pytest.mark.asyncio
async def test_resolve_provider_labels_distinguish_null_and_unknown() -> None:
    from app.core.database import AsyncSessionLocal
    from app.services.ops_metrics.llm_dimension_labels import resolve_dimension_labels

    async with AsyncSessionLocal() as db:
        labels = await resolve_dimension_labels(
            db,
            group_by="provider",
            raw_keys=[None, "unknown", "deepseek"],
        )
    assert labels[None]["label_state"] == LABEL_STATE_NOT_COLLECTED
    assert labels[None]["display_name"] == "未采集"
    assert labels["unknown"]["label_state"] == LABEL_STATE_UNRECOGNIZED
    assert labels["unknown"]["display_name"] == "未识别厂商"
    assert labels["deepseek"]["label_state"] == LABEL_STATE_LABELED
    assert labels["deepseek"]["display_name"] == "DeepSeek"


@pytest.mark.asyncio
async def test_label_coverage_zero_denominator() -> None:
    from app.core.database import AsyncSessionLocal
    from app.models.llm_attempt_event import LlmAttemptEvent as E
    from app.services.ops_metrics.llm_reliability import _label_coverage

    async with AsyncSessionLocal() as db:
        filters = [E.traffic_class == f"empty_{uuid.uuid4().hex[:8]}"]
        coverage = await _label_coverage(db, filters)
    assert coverage["total"] == 0
    for dim in coverage["dimensions"].values():
        assert dim["coverage_rate"] == 0.0
        assert dim["labeled"] == 0


def _seed_auth_error() -> str:
    engine = _sync_engine()
    now = datetime.now(timezone.utc)
    lid = uuid.uuid4().hex
    with engine.connect() as conn:
        conn.execute(text(
            "DELETE FROM llm_attempt_events WHERE traffic_class = :tc AND outcome = 'auth_error'"
        ), {"tc": _TC})
        conn.execute(text(
            "INSERT INTO llm_attempt_events "
            "(occurred_at, logical_call_id, attempt_no, traffic_class, purpose, "
            " provider, model_name, config_id, node_key, flow, method, outcome, "
            " http_status, latency_ms, prompt_tokens, completion_tokens, "
            " created_at, updated_at) "
            "VALUES (:ts, :lid, 1, :tc, 'review', 'deepseek', 'demo-model', "
            " 'cfg_demo', 'bid_review.review_item', 'bid_review', 'chat_json', "
            " 'auth_error', 401, 1200, 10, 0, now(), now())"
        ), {"ts": now - timedelta(minutes=1), "lid": lid, "tc": _TC})
        conn.commit()
    engine.dispose()
    return lid


def test_failure_api_filters_outcome_and_excludes_body(
    client: TestClient, admin_headers: dict,
) -> None:
    _seed_auth_error()
    resp = client.get(
        "/api/admin/ops/llm/failures",
        headers=admin_headers,
        params={"traffic_class": _TC, "window": "24h", "outcome": "auth_error"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["outcome"] == "auth_error"
    assert len(data["samples"]) >= 1
    sample = data["samples"][0]
    forbidden = {"prompt", "response", "raw_output", "content", "messages", "system_message"}
    assert forbidden.isdisjoint(sample.keys())
    assert "model_name" in sample
    assert "latency_ms" in sample
    # 样本必须带中文展示字段，禁止只吐技术键
    assert sample.get("provider_display") == "DeepSeek"
    assert sample.get("purpose_display") == "审查"
    assert sample.get("flow_display") == "投标审查"
    assert sample.get("node_display")
    assert sample["node_display"] != sample.get("node_key") or "未登记" in sample["node_display"]
    assert sample.get("config_display")
    agg = data["aggregates"][0] if data["aggregates"] else None
    if agg:
        assert agg.get("node_display")
        assert agg.get("config_display")

    bad = client.get(
        "/api/admin/ops/llm/failures",
        headers=admin_headers,
        params={"traffic_class": _TC, "window": "24h", "outcome": "not_a_real_outcome"},
    )
    assert bad.status_code == 200
    assert bad.json()["code"] == 400


def test_reliability_breakdown_includes_display_fields(
    client: TestClient, admin_headers: dict,
) -> None:
    engine = _sync_engine()
    now = datetime.now(timezone.utc)
    with engine.connect() as conn:
        conn.execute(text(
            "INSERT INTO llm_attempt_events "
            "(occurred_at, logical_call_id, attempt_no, traffic_class, purpose, "
            " provider, model_name, config_id, method, outcome, "
            " latency_ms, prompt_tokens, completion_tokens, created_at, updated_at) "
            "VALUES (:ts, :lid, 1, :tc, 'review', 'unknown', 'demo-model', "
            " 'cfg_demo', 'chat_json', 'valid', 500, 1, 1, now(), now())"
        ), {
            "ts": now,
            "lid": uuid.uuid4().hex,
            "tc": _TC,
        })
        conn.commit()
    engine.dispose()

    resp = client.get(
        "/api/admin/ops/llm/reliability",
        headers=admin_headers,
        params={"traffic_class": _TC, "window": "24h", "group_by": "provider"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "label_coverage" in data
    unknown_row = next(
        (r for r in data["breakdown"] if r.get("raw_key") == "unknown"),
        None,
    )
    assert unknown_row is not None
    assert unknown_row["display_name"] == "未识别厂商"
    assert unknown_row["label_state"] == LABEL_STATE_UNRECOGNIZED
