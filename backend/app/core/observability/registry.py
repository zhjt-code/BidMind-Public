"""指标注册器 (MetricsRegistry).

Phase 3 核心模块, 对齐 PromptRegistry 的设计:
  - MetricSpec: 指标元数据 (dataclass)
  - MetricsRegistry: 单例, 内存 + DB 双写, 支持 Redis INVALIDATE 广播
  - 运行时 API: register / get / list_specs / export_catalog

与 app/core/observability/metrics.py 的区别:
  - metrics.py 是"手工声明的 Prometheus 指标汇总"(已有/保留)
  - registry.py 是"函数级自动注册的业务语义指标目录", 每条指标关联到具体函数 source

前端从 /api/admin/metrics/catalog 读 MetricSpec 后, 对卡片渲染完全由元数据驱动.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Optional

from loguru import logger

# 类型别名
LayerType = Literal[
    "platform", "throughput", "stage", "quality",
    "llm", "vector", "stability", "ops", "frontend",
]
PillarType = Literal["quality", "reliability", "efficiency", "cost"]
DirectionType = Literal["higher_better", "lower_better", "neutral"]
KindType = Literal["counter", "histogram", "gauge"]
BusinessType = Literal["tender", "collusion", "writer", "platform", "all"]


@dataclass
class MetricSource:
    """指标所属的源码位置, 用于 Phase 5 的一键跳源码."""

    module: str = ""
    qualname: str = ""
    file: str = ""
    line: int = 0


@dataclass
class MetricSpec:
    """指标元数据 (类比 PromptRegistry 的 node spec).

    name: 全局唯一名称 (建议 <module>_<action>, 如 `tender_extract_chunk`)
    layer: 八层主分类 (用于 Dashboard 导航)
    pillar: 四维次级标签 (用于卡片染色)
    business: 所属业务 flow
    direction: 值增大是好是坏
    kind: Prometheus 指标类型
    labels: 自动从函数签名推导的 label 名 (不含 outcome 这种预设 label)
    unit: 计量单位 (可选, 纯展示用)
    thresholds: warn / critical 阈值
    description: 一句话描述
    source: 源码位置 (inspect 自动填充)
    """

    name: str
    layer: LayerType
    pillar: PillarType
    business: BusinessType = "all"
    direction: DirectionType = "neutral"
    kind: KindType = "counter"
    labels: list[str] = field(default_factory=list)
    unit: Optional[str] = None
    thresholds: dict[str, float] = field(default_factory=dict)
    description: str = ""
    source: MetricSource = field(default_factory=MetricSource)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


class MetricsRegistry:
    """线程安全的进程内指标注册器."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._specs: dict[str, MetricSpec] = {}
        # 延迟持有 prom 对象 (避免与其他模块重复注册同名 Prometheus 指标)
        self._prom_objects: dict[str, Any] = {}

    def register(self, spec: MetricSpec, prom_metric: Any = None) -> None:
        """注册一条 MetricSpec. 同名覆盖 (热更新)."""
        with self._lock:
            if spec.name in self._specs:
                prev = self._specs[spec.name]
                if prev != spec:
                    logger.debug(f"MetricsRegistry: 覆盖 {spec.name} (labels 变更?)")
            self._specs[spec.name] = spec
            if prom_metric is not None:
                self._prom_objects[spec.name] = prom_metric

    def get(self, name: str) -> Optional[MetricSpec]:
        with self._lock:
            return self._specs.get(name)

    def get_prom(self, name: str) -> Any:
        with self._lock:
            return self._prom_objects.get(name)

    def list_specs(self) -> list[MetricSpec]:
        with self._lock:
            return list(self._specs.values())

    def export_catalog(self) -> list[dict[str, Any]]:
        with self._lock:
            return [s.to_dict() for s in self._specs.values()]

    def clear(self) -> None:
        """仅用于测试."""
        with self._lock:
            self._specs.clear()
            self._prom_objects.clear()


_registry_singleton: Optional[MetricsRegistry] = None
_singleton_lock = threading.Lock()


def get_metrics_registry() -> MetricsRegistry:
    """获取进程内单例注册器."""
    global _registry_singleton
    if _registry_singleton is None:
        with _singleton_lock:
            if _registry_singleton is None:
                _registry_singleton = MetricsRegistry()
    return _registry_singleton
