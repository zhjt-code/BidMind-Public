"""周度 Advisor 写回/回滚/指纹冲突防御测试（API 层）。"""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.core.ai_settings import (
    PRIORITY_NORMAL,
    PRIORITY_PREFERRED,
    load_ai_settings,
)
from app.core.config import settings
from app.services.ops_advisor.weekly_reliability_report import resolve_week_start

_CST = ZoneInfo("Asia/Shanghai")


def _sync_engine():
    return create_engine(settings.DATABASE_URL.replace("+asyncpg", "+psycopg2"))


def _ensure_tables() -> None:
    engine = _sync_engine()
    with engine.connect() as conn:
        exists = conn.execute(text(
            "SELECT to_regclass('public.llm_weekly_advice') IS NOT NULL"
        )).scalar()
    engine.dispose()
    if not exists:
        pytest.skip("llm_weekly_advice 表不存在，请先执行 alembic upgrade")


def _seed_advice(purpose: str, proposed: dict[str, int]) -> int:
    engine = _sync_engine()
    ws = resolve_week_start()
    with engine.connect() as conn:
        conn.execute(text(
            "DELETE FROM llm_weekly_advice "
            "WHERE scope='purpose' AND target=:p AND week_start=:ws"
        ), {"p": purpose, "ws": ws})
        row = conn.execute(text(
            "INSERT INTO llm_weekly_advice "
            "(week_start, scope, target, advice_action, recommended_config_id, "
            " proposed_priority, evidence_json, rule_id, status, created_at, updated_at) "
            "VALUES (:ws, 'purpose', :p, 'promote', :rec, CAST(:pp AS jsonb), "
            " CAST(:ev AS jsonb), 'test_promote', 'pending', now(), now()) "
            "RETURNING id"
        ), {
            "ws": ws,
            "p": purpose,
            "rec": next(iter(proposed)),
            "pp": json.dumps(proposed),
            "ev": json.dumps({"purpose": purpose}),
        })
        advice_id = int(row.scalar())
        conn.commit()
    engine.dispose()
    return advice_id


@pytest.fixture()
def review_config_id():
    _ensure_tables()
    model = load_ai_settings()
    if not model.review_configs:
        pytest.skip("无 review_configs，跳过写回集成测试")
    cid = model.review_configs[0].id
    # 确保在池内，否则 set_pool_priority 会拒
    if cid not in model.review_pool_ids:
        pytest.skip(f"config {cid} 不在 review 调度池，跳过")
    return cid


def test_apply_rejects_fingerprint_mismatch(
    client: TestClient, admin_headers: dict, review_config_id: str,
) -> None:
    proposed = {review_config_id: PRIORITY_PREFERRED}
    advice_id = _seed_advice("review", proposed)
    resp = client.post(
        f"/api/admin/ops/llm/advice/{advice_id}/apply",
        headers=admin_headers,
        json={"expected_fingerprint": "deadbeefdeadbeef"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 409
    assert body["data"]["error_code"] == "fingerprint_mismatch"


def test_apply_and_rollback_roundtrip(
    client: TestClient, admin_headers: dict, review_config_id: str,
) -> None:
    settings_model = load_ai_settings()
    pool_ids = set(settings_model.get_pool_ids("review"))
    before = {
        cid: int(val)
        for cid, val in settings_model.get_pool_priority("review").items()
        if cid in pool_ids
    }
    # 构造合法提案：池内仅一个首选
    proposed = {cid: PRIORITY_NORMAL for cid in pool_ids}
    proposed[review_config_id] = PRIORITY_PREFERRED

    advice_id = _seed_advice("review", proposed)
    preview = client.get(
        f"/api/admin/ops/llm/advice/{advice_id}/preview",
        headers=admin_headers,
    )
    assert preview.status_code == 200, preview.text
    fp = preview.json()["data"]["current"]["fingerprint"]

    apply_resp = client.post(
        f"/api/admin/ops/llm/advice/{advice_id}/apply",
        headers=admin_headers,
        json={"expected_fingerprint": fp},
    )

    assert apply_resp.status_code == 200, apply_resp.text
    apply_body = apply_resp.json()
    assert apply_body["code"] == 200, apply_body
    assert apply_body["data"]["status"] == "applied"

    after = load_ai_settings().get_pool_priority("review")
    assert after.get(review_config_id) == PRIORITY_PREFERRED

    rb = client.post(
        f"/api/admin/ops/llm/advice/{advice_id}/rollback",
        headers=admin_headers,
    )
    assert rb.status_code == 200, rb.text
    assert rb.json()["data"]["status"] == "rolled_back"
    # before 可能不含该 key（默认 NORMAL），回滚后不应再是 PREFERRED 除非原本就是
    restored = load_ai_settings().get_pool_priority("review")
    assert restored.get(review_config_id, PRIORITY_NORMAL) == before.get(
        review_config_id, PRIORITY_NORMAL
    )


def test_resolve_week_start_is_monday() -> None:
    wed = datetime(2026, 8, 12, 15, 0, tzinfo=_CST)
    ws = resolve_week_start(wed)
    assert ws.weekday() == 0
    assert ws.date().isoformat() == "2026-08-03"
