"""启动期扫描器: 触发 @instrument 装饰器副作用, 把全量 MetricSpec 同步到 metric_catalog 表.

对齐 PromptRegistry.registry_sync 的设计.
"""

from __future__ import annotations

import importlib
import json
import pkgutil
from dataclasses import asdict
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.observability.registry import MetricSpec, get_metrics_registry

# 默认扫描的包 (lifespan 启动时触发 import 即可让装饰器登记)
DEFAULT_SCAN_PACKAGES: list[str] = [
    "app.api.routers",
    "app.core.ai",
    "app.core.ai.gates",
    "app.core.ai.critic",
    "app.core.tasks",
    "app.core.writer",
    "app.core.extractor",
    "app.core.collusion",
    "app.core.matcher",
    "app.core.parser",
    "app.core.vectorizer",
    "app.services",
]


def scan_packages(packages: list[str] | None = None) -> int:
    """遍历指定 package, 触发 import 以让 @instrument 装饰器登记.

    返回 registry 当前的 MetricSpec 数量.
    """
    targets = packages if packages is not None else DEFAULT_SCAN_PACKAGES
    imported = 0
    for pkg_name in targets:
        try:
            pkg = importlib.import_module(pkg_name)
        except Exception as e:
            logger.debug(f"scanner: import {pkg_name} 失败 (忽略): {e}")
            continue
        if not hasattr(pkg, "__path__"):
            continue
        for _finder, modname, _ispkg in pkgutil.walk_packages(pkg.__path__, prefix=f"{pkg_name}."):
            try:
                importlib.import_module(modname)
                imported += 1
            except Exception as e:
                logger.debug(f"scanner: import {modname} 失败 (忽略): {e}")
    reg_size = len(get_metrics_registry().list_specs())
    logger.info(f"MetricsRegistry scanner: 扫描 {imported} 个模块, 当前注册指标 {reg_size} 条")
    return reg_size


def _spec_to_row_dict(spec: MetricSpec) -> dict[str, Any]:
    d = asdict(spec)
    return {
        "name": d["name"],
        "layer": d["layer"],
        "pillar": d["pillar"],
        "business": d["business"],
        "direction": d["direction"],
        "kind": d["kind"],
        "description": d["description"] or "",
        "labels_json": json.dumps(d["labels"], ensure_ascii=False),
        "thresholds_json": json.dumps(d["thresholds"], ensure_ascii=False),
        "source_module": d["source"]["module"],
        "source_qualname": d["source"]["qualname"],
        "source_file": d["source"]["file"],
        "source_line": d["source"]["line"],
        "unit": d["unit"],
    }


async def upsert_catalog(db: AsyncSession) -> dict[str, int]:
    """把当前内存 registry 同步到 metric_catalog 表 (upsert 语义).

    返回 {"inserted": N, "updated": M, "total": K}.
    """
    # 延迟导入避免循环
    from app.models.metric_catalog import MetricCatalog

    specs = get_metrics_registry().list_specs()

    # 全量拉取现有 rows, 一轮匹配 (N 通常 < 500, 可接受)
    existing_rows = (await db.execute(select(MetricCatalog))).scalars().all()
    existing_by_name: dict[str, MetricCatalog] = {r.name: r for r in existing_rows}

    inserted = 0
    updated = 0
    for spec in specs:
        data = _spec_to_row_dict(spec)
        if spec.name in existing_by_name:
            row = existing_by_name[spec.name]
            changed = False
            for k, v in data.items():
                if getattr(row, k) != v:
                    setattr(row, k, v)
                    changed = True
            if changed:
                updated += 1
        else:
            row = MetricCatalog(**data)
            db.add(row)
            inserted += 1
    try:
        await db.commit()
    except Exception as e:
        logger.warning(f"upsert_catalog commit 失败: {e}")
        await db.rollback()

    total = len(specs)
    return {"inserted": inserted, "updated": updated, "total": total}


async def publish_invalidate() -> None:
    """通过 Redis pub/sub 通知其他进程刷新 catalog (对齐 PromptRegistry 机制)."""
    try:
        from app.core.redis import get_redis_pool
        pool = await get_redis_pool()
        if pool is not None:
            await pool.publish("bidmind:metrics:invalidate", "1")
    except Exception as e:
        logger.debug(f"publish_invalidate 失败 (忽略): {e}")
