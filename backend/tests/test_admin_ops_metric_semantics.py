import importlib.util
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://bidmind:bidmind@localhost:5432/bidmind_lite",
)
os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-key-not-for-prod")
os.environ.setdefault("ADMIN_PASSWORD", "CursorTest@2026_Strong")

MODULE_PATH = Path(__file__).resolve().parents[1] / "app" / "api" / "routers" / "admin_ops.py"


def _load_admin_ops_module():
    spec = importlib.util.spec_from_file_location("test_admin_ops_module", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _FakeSession:
    def __init__(self, values):
        self._values = list(values)

    async def execute(self, _statement):
        if not self._values:
            raise AssertionError("unexpected execute call")
        return _ScalarResult(self._values.pop(0))


def test_layer_quality_uses_current_failure_state_labels(monkeypatch):
    module = _load_admin_ops_module()
    values = {
        ("failure_state_distribution", "completed"): 8.0,
        ("failure_state_distribution", "degraded"): 1.0,
        ("failure_state_distribution", "needs_review"): 1.0,
        ("failure_state_distribution", "inconclusive"): 0.0,
        ("failure_state_distribution", "failed"): 0.0,
    }

    def fake_prom_sum(metric_name, labels=None):
        if labels and "state" in labels:
            return values.get((metric_name, labels["state"]), 0.0)
        return 0.0

    monkeypatch.setattr(module, "_prom_counter_sum", fake_prom_sum)

    metrics = module._layer_quality()
    target = next(item for item in metrics if item["name"] == "failure_state_perfect_ratio")

    assert target["description"] == "直接完成占比"
    assert target["value"] == 0.8
    assert target["verdict"] == "stable"
    assert target["reason"] == "completed=8/total=10"
    assert target["breakdown"] == {
        "completed": 8,
        "degraded": 1,
        "needs_review": 1,
        "inconclusive": 0,
        "failed": 0,
    }


def test_layer_quality_is_neutral_when_no_failure_state_samples(monkeypatch):
    module = _load_admin_ops_module()
    monkeypatch.setattr(module, "_prom_counter_sum", lambda *_args, **_kwargs: 0.0)

    metrics = module._layer_quality()
    target = next(item for item in metrics if item["name"] == "failure_state_perfect_ratio")

    assert target["value"] == 0.0
    assert target["verdict"] == "neutral"
    assert target["reason"] == "当前时间范围内暂无失败状态样本"

    flow_card = next(item for item in metrics if item["name"] == "critic_flow_health_writer")
    assert flow_card["value"] == 0
    assert flow_card["verdict"] == "neutral"
    assert flow_card["reason"] == "当前时间范围内暂无 Critic 样本"


def test_critic_flow_breakdown_counts_by_flow_and_rates(monkeypatch):
    module = _load_admin_ops_module()
    values = {
        ("judge_outcome_total", "investigate", "verdict", "agree"): 7.0,
        ("judge_outcome_total", "investigate", "verdict", "disagree"): 1.0,
        ("judge_outcome_total", "investigate", "verdict", "need_review"): 2.0,
        ("judge_inconclusive_total", "investigate", "reason", "error"): 1.0,
        ("cove_outcome_total", "investigate", "outcome", "agree"): 4.0,
        ("cove_outcome_total", "investigate", "outcome", "disagree"): 1.0,
        ("cove_outcome_total", "investigate", "outcome", "inconclusive"): 0.0,
        ("cove_outcome_total", "investigate", "outcome", "skipped"): 5.0,
    }

    def fake_prom_sum(metric_name, labels=None):
        if not labels:
            return 0.0
        flow = labels.get("flow")
        for key_name in ("verdict", "outcome", "reason"):
            if key_name in labels:
                return values.get((metric_name, flow, key_name, labels[key_name]), 0.0)
        return sum(
            value for (name, item_flow, _key_name, _key_value), value in values.items()
            if name == metric_name and item_flow == flow
        )

    monkeypatch.setattr(module, "_prom_counter_sum", fake_prom_sum)

    breakdown = module._critic_flow_breakdown()
    investigate = breakdown["investigate"]

    assert investigate["label"] == "投标审查"
    assert investigate["judge"] == {
        "total": 10,
        "agree": 7,
        "disagree": 1,
        "need_review": 2,
        "inconclusive": 1,
        "disagree_rate": 0.1,
        "inconclusive_rate": 0.0909,
    }
    assert investigate["cove"] == {
        "total": 10,
        "agree": 4,
        "disagree": 1,
        "inconclusive": 0,
        "skipped": 5,
        "disagree_rate": 0.1,
        "inconclusive_rate": 0.0,
    }
    assert breakdown["collusion"]["judge"]["total"] == 0
    assert breakdown["writer"]["cove"]["total"] == 0


def test_layer_quality_adds_critic_flow_cards(monkeypatch):
    module = _load_admin_ops_module()

    def fake_breakdown():
        return {
            "investigate": {
                "flow": "investigate",
                "label": "投标审查",
                "judge": {
                    "total": 10,
                    "agree": 7,
                    "disagree": 1,
                    "need_review": 2,
                    "inconclusive": 0,
                    "disagree_rate": 0.1,
                    "inconclusive_rate": 0.0,
                },
                "cove": {
                    "total": 5,
                    "agree": 4,
                    "disagree": 1,
                    "inconclusive": 0,
                    "skipped": 0,
                    "disagree_rate": 0.2,
                    "inconclusive_rate": 0.0,
                },
            },
            "collusion": {
                "flow": "collusion",
                "label": "串标审查",
                "judge": {
                    "total": 0,
                    "agree": 0,
                    "disagree": 0,
                    "need_review": 0,
                    "inconclusive": 0,
                    "disagree_rate": 0.0,
                    "inconclusive_rate": 0.0,
                },
                "cove": {
                    "total": 0,
                    "agree": 0,
                    "disagree": 0,
                    "inconclusive": 0,
                    "skipped": 0,
                    "disagree_rate": 0.0,
                    "inconclusive_rate": 0.0,
                },
            },
            "writer": {
                "flow": "writer",
                "label": "标书编写",
                "judge": {
                    "total": 5,
                    "agree": 2,
                    "disagree": 2,
                    "need_review": 1,
                    "inconclusive": 2,
                    "disagree_rate": 0.4,
                    "inconclusive_rate": 0.2857,
                },
                "cove": {
                    "total": 3,
                    "agree": 1,
                    "disagree": 2,
                    "inconclusive": 0,
                    "skipped": 0,
                    "disagree_rate": 0.6667,
                    "inconclusive_rate": 0.0,
                },
            },
        }

    monkeypatch.setattr(module, "_prom_counter_sum", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(module, "_critic_flow_breakdown", fake_breakdown)

    metrics = module._layer_quality()
    cards = [m for m in metrics if m["name"].startswith("critic_flow_health_")]

    assert [c["name"] for c in cards] == [
        "critic_flow_health_investigate",
        "critic_flow_health_collusion",
        "critic_flow_health_writer",
    ]
    assert cards[0]["verdict"] == "stable"
    assert cards[0]["value"] == 15
    assert cards[1]["verdict"] == "neutral"
    assert cards[2]["verdict"] == "critical"
    assert cards[2]["breakdown"]["flow"] == "writer"


def test_layer_stage_is_neutral_when_no_duration_samples(monkeypatch):
    module = _load_admin_ops_module()
    monkeypatch.setattr(
        module,
        "_prom_histogram_stats",
        lambda _name: {"count": 0.0, "sum": 0.0, "avg": 0.0, "p95": 0.0},
    )

    metrics = module._layer_stage()

    assert all(item["verdict"] == "neutral" for item in metrics)
    assert all("暂无" in item["reason"] for item in metrics)


@pytest.mark.asyncio
async def test_layer_llm_error_rate_is_neutral_when_no_calls(monkeypatch):
    module = _load_admin_ops_module()
    monkeypatch.setattr(module, "_prom_counter_sum", lambda *_args, **_kwargs: 0.0)
    db = _FakeSession([0, 0, 0])

    metrics = await module._layer_llm(
        db,
        datetime(2026, 4, 1, tzinfo=timezone.utc),
        datetime(2026, 3, 1, tzinfo=timezone.utc),
    )
    target = next(item for item in metrics if item["name"] == "llm_error_rate")

    assert target["verdict"] == "neutral"
    assert target["reason"] == "当前时间范围内暂无模型调用样本"


@pytest.mark.asyncio
async def test_layer_vector_ratios_are_neutral_when_no_samples(monkeypatch):
    module = _load_admin_ops_module()
    monkeypatch.setattr(module, "_prom_counter_sum", lambda *_args, **_kwargs: 0.0)
    db = _FakeSession([0, 0])

    metrics = await module._layer_vector(
        db,
        datetime(2026, 4, 1, tzinfo=timezone.utc),
        datetime(2026, 3, 1, tzinfo=timezone.utc),
    )
    degrade = next(item for item in metrics if item["name"] == "vector_degrade_to_cpu_ratio")
    clean = next(item for item in metrics if item["name"] == "chunk_clean_ratio")

    assert degrade["verdict"] == "neutral"
    assert degrade["reason"] == "当前时间范围内暂无活跃向量任务"
    assert clean["verdict"] == "neutral"
    assert clean["reason"] == "当前时间范围内暂无文本质量样本"


def test_layer_stability_is_neutral_when_no_samples(monkeypatch):
    module = _load_admin_ops_module()

    class _EmptyRegistry:
        def collect(self):
            return []

    monkeypatch.setattr(module, "PROM_REGISTRY", _EmptyRegistry())
    metrics = module._layer_stability()

    assert all(item["verdict"] == "neutral" for item in metrics)
    assert all(item["reason"] == "当前时间范围内暂无稳定性样本" for item in metrics)
