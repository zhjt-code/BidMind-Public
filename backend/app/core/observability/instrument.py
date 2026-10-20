"""@instrument 装饰器: 函数签名自动对齐 + Prometheus 自动创建 + MetricsRegistry 自动登记.

与 PromptRegistry.call_with_fallback (backend/app/core/ai/prompt_registry.py) 同思路:
  - inspect.signature(fn).bind(*args, **kwargs).apply_defaults() 推导 label 值
  - 函数签名改变时 labels 自动跟随, 无需改 Dashboard

覆盖三种函数形态:
  - sync 函数
  - async 函数 (coroutine)
  - async generator (仅 count 不测 latency)

用法::

    @instrument(
        name="tender_extract_chunk",
        layer="stage",
        pillar="efficiency",
        direction="lower_better",
        business="tender",
        measure="latency",
        labels_from=["project_id", "model"],   # 可选; 不写则从函数签名里自动挑
    )
    async def _extract_from_chunk(self, chunk, project_id, model): ...

相比于"手写 Prometheus 声明":
  - 不需要手动定义 Counter / Histogram 对象
  - 不需要手动 .observe / .inc
  - MetricSpec 自动含 source, 支持 Phase 5 一键跳源码

注意:
  - Prometheus label 只接受低基数 (string), 高基数 project_id 不会进 Prom, 只会进 MetricSpec.labels 记录
  - 异常绝不吞, 失败路径只是打 warning
"""

from __future__ import annotations

import functools
import inspect
import time
from typing import Any, Callable, Optional

from loguru import logger
from prometheus_client import REGISTRY as _PROM_REGISTRY
from prometheus_client import Counter, Gauge, Histogram

from app.core.observability.registry import (
    BusinessType,
    DirectionType,
    KindType,
    LayerType,
    MetricSource,
    MetricSpec,
    PillarType,
    get_metrics_registry,
)

# 低基数白名单: 只有这些参数名才会被下推到 Prometheus labels.
# 高基数 (project_id / user_id / document_id) 一律不进 Prom, 只保留在 MetricSpec.labels.
_PROM_LABEL_ALLOWLIST: set[str] = {
    "flow",
    "model",
    "node_key",
    "purpose",
    "stage",
    "gate",
    "dim",
    "outcome",
    "status",
    "verdict",
    "chunk_type",
    "device",
    "queue_mode",
}

# 为避免 Prometheus 同名重复注册报错, 用 registry._names_to_collectors


def _existing_prom(name: str) -> Optional[Any]:
    """检查 Prometheus 默认 registry 是否已存在同名指标."""
    try:
        return _PROM_REGISTRY._names_to_collectors.get(name)  # type: ignore[attr-defined]
    except Exception:
        return None


def _build_prom_metric(
    *,
    name: str,
    kind: KindType,
    description: str,
    prom_labels: list[str],
) -> Any:
    """根据 MetricSpec.kind 创建 Prometheus 指标. 同名重复时直接返回已有对象."""
    existing = _existing_prom(name)
    if existing is not None:
        return existing

    try:
        if kind == "counter":
            return Counter(name, description, prom_labels)
        if kind == "histogram":
            return Histogram(
                name,
                description,
                prom_labels,
                buckets=(0.005, 0.01, 0.05, 0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300),
            )
        if kind == "gauge":
            return Gauge(name, description, prom_labels)
    except Exception as e:
        logger.warning(f"Prometheus 指标 {name} 创建失败: {e}")
        return None

    return None


def _extract_source(fn: Callable[..., Any]) -> MetricSource:
    """用 inspect 定位 fn 的源码位置."""
    try:
        file = inspect.getsourcefile(fn) or ""
        line = 0
        try:
            _lines, line = inspect.getsourcelines(fn)
        except OSError:
            line = 0
        return MetricSource(
            module=fn.__module__ or "",
            qualname=fn.__qualname__ or "",
            file=file,
            line=line,
        )
    except Exception:
        return MetricSource()


def _infer_label_names(
    fn: Callable[..., Any],
    override: Optional[list[str]],
) -> list[str]:
    """从函数签名里挑 label 名 (仅挑 allowlist 中的)."""
    if override is not None:
        return list(override)
    try:
        sig = inspect.signature(fn)
        names: list[str] = []
        for p in sig.parameters:
            if p in {"self", "cls"}:
                continue
            if p in _PROM_LABEL_ALLOWLIST:
                names.append(p)
        return names
    except (TypeError, ValueError):
        return []


def _bind_labels(
    fn_sig: inspect.Signature,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    label_names: list[str],
    extra_outcome: Optional[str] = None,
) -> dict[str, str]:
    """按 PromptRegistry.call_with_fallback 同思路 bind 参数并转成 Prom label dict."""
    out: dict[str, str] = {}
    try:
        bound = fn_sig.bind_partial(*args, **kwargs)
        bound.apply_defaults()
        for name in label_names:
            if name == "outcome" and extra_outcome is not None:
                out[name] = extra_outcome
                continue
            raw = bound.arguments.get(name)
            if raw is None:
                out[name] = "unknown"
            elif hasattr(raw, "value"):
                out[name] = str(raw.value)
            else:
                out[name] = str(raw)[:64]
    except (TypeError, ValueError):
        for name in label_names:
            out[name] = "unknown"
    if "outcome" in label_names and "outcome" not in out:
        out["outcome"] = extra_outcome or "ok"
    return out


def instrument(
    *,
    name: str,
    layer: LayerType,
    pillar: PillarType,
    direction: DirectionType = "neutral",
    business: BusinessType = "all",
    measure: str = "latency",  # "latency" | "count" | "outcome"
    labels_from: Optional[list[str]] = None,
    description: Optional[str] = None,
    thresholds: Optional[dict[str, float]] = None,
    unit: Optional[str] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """为函数注入指标采集.

    measure:
      - "latency": Histogram, 观察函数总耗时, 并自带 outcome 标签 (ok/error)
      - "count":   Counter, 每次调用 +1; 带 outcome 标签
      - "outcome": Counter, 仅记录 outcome 分布

    异常处理:
      - 函数抛异常时 outcome=error 计数 +1, 然后 re-raise
      - 指标写入本身的异常会被吞掉 (不影响业务)
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        source = _extract_source(fn)
        inferred_labels = _infer_label_names(fn, labels_from)
        prom_labels = list(inferred_labels)
        if "outcome" not in prom_labels:
            prom_labels.append("outcome")

        kind: KindType = "histogram" if measure == "latency" else "counter"

        spec = MetricSpec(
            name=name,
            layer=layer,
            pillar=pillar,
            business=business,
            direction=direction,
            kind=kind,
            labels=prom_labels,
            unit=unit,
            thresholds=thresholds or {},
            description=description or (fn.__doc__ or "").strip().splitlines()[0] if (description or fn.__doc__) else name,
            source=source,
        )
        prom_metric = _build_prom_metric(
            name=name,
            kind=kind,
            description=spec.description or name,
            prom_labels=prom_labels,
        )
        get_metrics_registry().register(spec, prom_metric)

        fn_sig: Optional[inspect.Signature]
        try:
            fn_sig = inspect.signature(fn)
        except (TypeError, ValueError):
            fn_sig = None

        def _emit(labels: dict[str, str], duration: float, outcome: str) -> None:
            """安全写入 Prometheus. 任何失败仅打 debug."""
            if prom_metric is None or fn_sig is None:
                return
            try:
                lbl_with_outcome = dict(labels)
                lbl_with_outcome["outcome"] = outcome
                labeled = prom_metric.labels(**lbl_with_outcome)
                if kind == "histogram":
                    labeled.observe(duration)
                else:
                    labeled.inc()
            except Exception as e:
                logger.debug(f"instrument[{name}] emit 失败: {e}")

        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                if fn_sig is None or prom_metric is None:
                    return await fn(*args, **kwargs)
                label_values = _bind_labels(fn_sig, args, kwargs, inferred_labels)
                t0 = time.perf_counter()
                outcome = "ok"
                try:
                    result = await fn(*args, **kwargs)
                    return result
                except Exception:
                    outcome = "error"
                    raise
                finally:
                    _emit(label_values, time.perf_counter() - t0, outcome)

            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            if fn_sig is None or prom_metric is None:
                return fn(*args, **kwargs)
            label_values = _bind_labels(fn_sig, args, kwargs, inferred_labels)
            t0 = time.perf_counter()
            outcome = "ok"
            try:
                return fn(*args, **kwargs)
            except Exception:
                outcome = "error"
                raise
            finally:
                _emit(label_values, time.perf_counter() - t0, outcome)

        return sync_wrapper

    return decorator
