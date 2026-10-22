"""P0-A: /admin/ops/llm/reliability 聚合口径防御测试。

锁定契约:
- 逻辑调用量 = distinct logical_call_id; 物理请求量 = 行数;
- 重试率 / 首次有效率 / 最终有效率按逻辑调用口径;
- 空响应率 / 结构化失败率按物理请求口径;
- 不同 traffic_class 互相隔离(admin_test 不进 regression 统计);
- 未认证/非管理员访问被拒绝。

数据准备通过同步 SQL 直插 llm_attempt_events(traffic_class=regression,
先清理同类旧数据保证确定性), 不依赖真实 LLM 调用。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.core.config import settings

_TC = "regression"


def _sync_engine():
    return create_engine(settings.DATABASE_URL.replace("+asyncpg", "+psycopg2"))


def _seed_events() -> dict:
    """构造确定性样本:

    - 逻辑A(chat_json): rate_limited -> empty -> valid  (重试, 首次无效, 最终有效)
    - 逻辑B(chat_json): valid                            (首次即有效)
    - 逻辑C(chat_with_validation): schema_invalid        (最终无效)
    - 一条 admin_test 流量 valid, 必须被 regression 查询排除
    """
    engine = _sync_engine()
    now = datetime.now(timezone.utc)
    a, b, c = uuid.uuid4().hex, uuid.uuid4().hex, uuid.uuid4().hex
    rows = [
        # (logical, attempt_no, traffic, method, outcome, rl_kind, queue_wait, latency)
        (a, 1, _TC, "chat_json", "rate_limited", "rate_limit", 30, 900),
        (a, 2, _TC, "chat_json", "empty", None, None, 1100),
        (a, 3, _TC, "chat_json", "valid", None, None, 1500),
        (b, 1, _TC, "chat_json", "valid", None, 10, 800),
        (c, 1, _TC, "chat_with_validation", "schema_invalid", None, 5, 2000),
        (uuid.uuid4().hex, 1, "admin_test", "chat", "valid", None, 0, 100),
    ]
    with engine.connect() as conn:
        conn.execute(text(
            "DELETE FROM llm_attempt_events WHERE traffic_class IN ('regression', 'admin_test')"
        ))
        for logical, no, tc, method, outcome, rl, qw, lat in rows:
            conn.execute(text(
                "INSERT INTO llm_attempt_events "
                "(occurred_at, logical_call_id, attempt_no, traffic_class, purpose, "
                " provider, model_name, config_id, method, outcome, rate_limit_kind, "
                " queue_wait_ms, latency_ms, prompt_tokens, completion_tokens, "
                " created_at, updated_at) "
                "VALUES (:ts, :lid, :no, :tc, 'review', 'deepseek', 'demo-model', "
                " 'cfg_demo', :method, :outcome, :rl, :qw, :lat, 10, 5, now(), now())"
            ), {
                "ts": now - timedelta(minutes=no),
                "lid": logical, "no": no, "tc": tc, "method": method,
                "outcome": outcome, "rl": rl, "qw": qw, "lat": lat,
            })
        conn.commit()
    engine.dispose()
    return {"a": a, "b": b, "c": c}


@pytest.fixture(scope="module")
def seeded(client: TestClient) -> dict:
    return _seed_events()


def _get(client: TestClient, headers: dict, **params) -> dict:
    resp = client.get(
        "/api/admin/ops/llm/reliability",
        headers=headers,
        params={"traffic_class": _TC, "window": "24h", **params},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_requires_admin(client: TestClient, auth_headers: dict) -> None:
    resp = client.get("/api/admin/ops/llm/reliability")
    assert resp.status_code in (401, 403)
    resp = client.get("/api/admin/ops/llm/reliability", headers=auth_headers)
    assert resp.status_code == 403


def test_logical_and_physical_denominators(
    client: TestClient, admin_headers: dict, seeded: dict,
) -> None:
    data = _get(client, admin_headers)
    totals = data["totals"]
    assert totals["logical_calls"] == 3
    assert totals["physical_attempts"] == 5
    assert totals["retried_calls"] == 1
    assert totals["retry_rate"] == pytest.approx(1 / 3, abs=1e-3)


def test_first_and_final_valid_rates(
    client: TestClient, admin_headers: dict, seeded: dict,
) -> None:
    totals = _get(client, admin_headers)["totals"]
    # 首次有效: 仅逻辑B -> 1/3
    assert totals["first_valid_rate"] == pytest.approx(1 / 3, abs=1e-3)
    # 最终有效: 逻辑A(第3次valid) + 逻辑B -> 2/3
    assert totals["final_valid_rate"] == pytest.approx(2 / 3, abs=1e-3)


def test_outcome_and_structured_rates(
    client: TestClient, admin_headers: dict, seeded: dict,
) -> None:
    totals = _get(client, admin_headers)["totals"]
    assert totals["outcome_counts"]["valid"] == 2
    assert totals["outcome_counts"]["empty"] == 1
    assert totals["outcome_counts"]["rate_limited"] == 1
    assert totals["outcome_counts"]["schema_invalid"] == 1
    assert totals["empty_rate"] == pytest.approx(1 / 5, abs=1e-3)
    # 全部 5 条均为结构化方法(chat_json/chat_with_validation), 失败仅 schema_invalid 1 条
    assert totals["structured_attempts"] == 5
    assert totals["structured_fail_rate"] == pytest.approx(1 / 5, abs=1e-3)
    assert totals["rate_limit_kinds"].get("rate_limit") == 1


def test_latency_and_queue_percentiles_present(
    client: TestClient, admin_headers: dict, seeded: dict,
) -> None:
    totals = _get(client, admin_headers)["totals"]
    assert totals["latency_p50_ms"] > 0
    assert totals["latency_p95_ms"] >= totals["latency_p50_ms"]
    # 3 条记录了 queue_wait(每个逻辑调用的首个 attempt)
    assert totals["queue_p95_ms"] >= totals["queue_p50_ms"] >= 0


def test_traffic_class_isolation(
    client: TestClient, admin_headers: dict, seeded: dict,
) -> None:
    regression = _get(client, admin_headers)
    admin_test = _get(client, admin_headers, traffic_class="admin_test")
    assert regression["totals"]["physical_attempts"] == 5
    assert admin_test["totals"]["physical_attempts"] == 1, (
        "admin_test 流量必须与 regression 分开统计"
    )


def test_breakdown_and_timeseries(
    client: TestClient, admin_headers: dict, seeded: dict,
) -> None:
    data = _get(client, admin_headers, group_by="model")
    assert data["group_by"] == "model"
    assert len(data["breakdown"]) == 1
    row = data["breakdown"][0]
    assert row["key"] == "demo-model"
    assert row["attempts"] == 5
    assert row["valid_rate"] == pytest.approx(2 / 5, abs=1e-3)
    assert row["structured_fail_count"] == 1
    assert len(data["timeseries"]) >= 1
    assert sum(b["attempts"] for b in data["timeseries"]) == 5


def test_invalid_params_fall_back(
    client: TestClient, admin_headers: dict, seeded: dict,
) -> None:
    resp = client.get(
        "/api/admin/ops/llm/reliability",
        headers=admin_headers,
        params={"window": "bogus", "traffic_class": "bogus", "group_by": "bogus"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["window"] == "24h"
    assert data["traffic_class"] == "production"
    assert data["group_by"] is None
