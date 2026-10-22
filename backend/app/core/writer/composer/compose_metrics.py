"""写标生成低基数观测。

职责: 注册阶段耗时、缓存、DB roundtrip、commit、Judge 与规划/篇幅调整计数。
不做: 不把 project_id/node_id/标题写入标签，不记录完整文档内容。
"""

from __future__ import annotations

from prometheus_client import REGISTRY, Counter, Histogram

from app.core.observability.registry import MetricSpec, get_metrics_registry

LABEL_WHITELIST = frozenset({"stage", "route", "kind", "outcome", "planning_mode"})

PLANNING_MODE_VALUES = frozenset(
    {
        "legacy_flag_off",
        "legacy_no_project",
        "legacy_bucket",
        "planned",
        "planned_new",
        "planned_reused",
        "planned_sanitized",
        "legacy_fallback",
        "unspecified",
        "other",
    }
)


def planning_mode_label(value: str | None) -> str:
    if value in PLANNING_MODE_VALUES:
        return value
    if value:
        return "other"
    return "unspecified"

_REGISTERED = False


def _counter(name: str, documentation: str, labels: list[str]) -> Counter:
    try:
        return Counter(name, documentation, labels)
    except ValueError:
        return REGISTRY._names_to_collectors[name]  # type: ignore[return-value]


def _histogram(name: str, documentation: str, labels: list[str]) -> Histogram:
    try:
        return Histogram(
            name,
            documentation,
            labels,
            buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 15.0, 60.0, 180.0),
        )
    except ValueError:
        return REGISTRY._names_to_collectors[name]  # type: ignore[return-value]


writer_compose_phase_seconds = _histogram(
    "writer_compose_phase_seconds",
    "写标生成阶段耗时",
    ["stage", "route", "outcome"],
)
writer_compose_cache_total = _counter(
    "writer_compose_cache_total",
    "写标任务缓存命中/未命中",
    ["kind", "outcome"],
)
writer_compose_db_roundtrip_total = _counter(
    "writer_compose_db_roundtrip_total",
    "写标生成数据库往返次数",
    ["kind", "outcome"],
)
writer_compose_commit_total = _counter(
    "writer_compose_commit_total",
    "写标生成提交次数",
    ["kind", "outcome"],
)
writer_compose_judge_total = _counter(
    "writer_compose_judge_total",
    "写标 Judge 入队与 drain",
    ["kind", "outcome"],
)
writer_compose_planning_mode_total = _counter(
    "writer_compose_planning_mode_total",
    "章节规划模式触发次数",
    ["planning_mode", "outcome"],
)
writer_compose_length_adjust_total = _counter(
    "writer_compose_length_adjust_total",
    "篇幅调整触发次数",
    ["outcome"],
)

COMPOSE_METRICS = (
    writer_compose_phase_seconds,
    writer_compose_cache_total,
    writer_compose_db_roundtrip_total,
    writer_compose_commit_total,
    writer_compose_judge_total,
    writer_compose_planning_mode_total,
    writer_compose_length_adjust_total,
)


def _assert_labels_allowed(labels: tuple[str, ...] | list[str]) -> None:
    extra = set(labels) - LABEL_WHITELIST
    if extra:
        raise ValueError(f"compose metrics labels not allowed: {sorted(extra)}")


def register_compose_metrics() -> None:
    """
    职责: 把低基数写标生成指标登记到现有 MetricsRegistry。
    不做: 不采集业务主键，不改变生成语义。
    """
    global _REGISTERED
    for metric in COMPOSE_METRICS:
        _assert_labels_allowed(metric._labelnames)
    if _REGISTERED:
        return
    registry = get_metrics_registry()
    specs = (
        MetricSpec(
            name="writer_compose_phase_seconds",
            layer="stage",
            pillar="efficiency",
            business="writer",
            direction="lower_better",
            kind="histogram",
            labels=["stage", "route", "outcome"],
            description="写标生成阶段耗时",
        ),
        MetricSpec(
            name="writer_compose_cache_total",
            layer="stage",
            pillar="efficiency",
            business="writer",
            direction="higher_better",
            kind="counter",
            labels=["kind", "outcome"],
            description="写标任务缓存命中/未命中",
        ),
        MetricSpec(
            name="writer_compose_db_roundtrip_total",
            layer="stage",
            pillar="efficiency",
            business="writer",
            direction="lower_better",
            kind="counter",
            labels=["kind", "outcome"],
            description="写标生成数据库往返次数",
        ),
        MetricSpec(
            name="writer_compose_commit_total",
            layer="stage",
            pillar="efficiency",
            business="writer",
            direction="lower_better",
            kind="counter",
            labels=["kind", "outcome"],
            description="写标生成提交次数",
        ),
        MetricSpec(
            name="writer_compose_judge_total",
            layer="stage",
            pillar="efficiency",
            business="writer",
            direction="neutral",
            kind="counter",
            labels=["kind", "outcome"],
            description="写标 Judge 入队与 drain",
        ),
        MetricSpec(
            name="writer_compose_planning_mode_total",
            layer="quality",
            pillar="quality",
            business="writer",
            direction="neutral",
            kind="counter",
            labels=["planning_mode", "outcome"],
            description="章节规划模式触发次数",
        ),
        MetricSpec(
            name="writer_compose_length_adjust_total",
            layer="quality",
            pillar="quality",
            business="writer",
            direction="neutral",
            kind="counter",
            labels=["outcome"],
            description="篇幅调整触发次数",
        ),
    )
    for spec, metric in zip(specs, COMPOSE_METRICS):
        registry.register(spec, metric)
    _REGISTERED = True


register_compose_metrics()
