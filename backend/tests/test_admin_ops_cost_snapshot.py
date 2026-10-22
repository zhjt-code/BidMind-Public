from datetime import datetime, timezone

import pytest

from app.api.routers.admin_ops import _cost_snapshot


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)

    async def execute(self, _statement):
        if not self._responses:
            raise AssertionError("unexpected execute call")
        return _FakeResult(self._responses.pop(0))


WINDOW = datetime(2026, 4, 1, tzinfo=timezone.utc)
BASELINE = datetime(2026, 3, 1, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_cost_snapshot_keeps_usage_and_zero_credits():
    db = _FakeSession([
        [(1, 10, 2), (2, 5, 0)],
        [(1, 6500, 2000), (3, 13000, 4000)],
        [(1, "bid_review"), (2, "bid_writing"), (3, "collusion_check")],
    ])

    snapshot = await _cost_snapshot(db, WINDOW, BASELINE)

    assert snapshot["project_count"] == 3
    assert snapshot["page_count"] == 15
    assert snapshot["input_tokens"] == 19500
    assert snapshot["output_tokens"] == 6000
    assert snapshot["tokens_per_project"] == 8500
    assert snapshot["tokens_per_page"] == 1700
    assert snapshot["total_credits"] == 0
    assert snapshot["project_avg_credits"] == 0.0
    assert snapshot["credit_yuan_rate"] == 0.0
    assert snapshot["estimated_yuan"] == 0.0
    assert [row["flow"] for row in snapshot["by_flow"]] == [
        "bid_writing",
        "bid_review",
        "collusion_check",
    ]
    by_flow = {row["flow"]: row for row in snapshot["by_flow"]}
    assert by_flow["bid_review"]["input_tokens"] == 6500
    assert by_flow["collusion_check"]["input_tokens"] == 13000
    assert all(row["total_credits"] == 0 for row in snapshot["by_flow"])
