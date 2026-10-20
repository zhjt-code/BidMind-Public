"""写标生成指标标签白名单与注册契约。"""

from __future__ import annotations

from app.core.observability.registry import get_metrics_registry
from app.core.writer.composer.compose_metrics import (
    COMPOSE_METRICS,
    LABEL_WHITELIST,
    PLANNING_MODE_VALUES,
    planning_mode_label,
    register_compose_metrics,
)


def test_compose_metric_labels_are_whitelisted() -> None:
    forbidden = {"project_id", "node_id", "title", "task_name"}
    for metric in COMPOSE_METRICS:
        labels = set(metric._labelnames)
        assert labels <= LABEL_WHITELIST
        assert labels.isdisjoint(forbidden)


def test_compose_metrics_register_into_existing_registry() -> None:
    register_compose_metrics()
    registry = get_metrics_registry()
    names = {spec.name for spec in registry.list_specs()}
    expected = {
        "writer_compose_phase_seconds",
        "writer_compose_cache_total",
        "writer_compose_db_roundtrip_total",
        "writer_compose_commit_total",
        "writer_compose_judge_total",
        "writer_compose_planning_mode_total",
        "writer_compose_length_adjust_total",
    }
    assert expected <= names
    for name in expected:
        spec = registry.get(name)
        assert spec is not None
        assert set(spec.labels) <= LABEL_WHITELIST
        assert spec.business == "writer"


def test_planning_mode_label_is_bounded() -> None:
    assert planning_mode_label("planned_reused") == "planned_reused"
    assert planning_mode_label("legacy_flag_off") == "legacy_flag_off"
    assert planning_mode_label(None) == "unspecified"
    assert planning_mode_label("unexpected-reason") == "other"
    assert "project_id" not in PLANNING_MODE_VALUES
