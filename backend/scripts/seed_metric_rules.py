"""为已注册的 metric_catalog 条目批量 seed 默认 metric_rule.

启发式映射:
  - kind=histogram (latency) -> ewma (3 sigma 控制带)
  - kind=gauge 且 name 含 ratio -> mk (Mann-Kendall 趋势)
  - kind=counter -> ratio (环比)
  - 其余 -> ratio 兜底

只插入不覆盖: 若某 metric_name 已有规则, 跳过.

用法::
    cd backend
    python -m scripts.seed_metric_rules
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# 确保可以 import app.*
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from loguru import logger
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.metric_catalog import MetricCatalog
from app.models.metric_rule import MetricRule


def _choose_algorithm(catalog: MetricCatalog) -> tuple[str, dict]:
    """根据 catalog 的 kind/direction/name 选默认算法."""
    name = catalog.name.lower()
    if catalog.kind == "histogram":
        return "ewma", {"lam": 0.2, "sigma": 3}
    if catalog.kind == "gauge" and ("ratio" in name or "rate" in name):
        return "mk", {"alpha": 0.05}
    if catalog.kind == "counter":
        return "ratio", {"baseline_split": 0.5, "warn": 0.15, "critical": 0.35}
    return "ratio", {"baseline_split": 0.5, "warn": 0.1, "critical": 0.25}


async def seed() -> dict:
    async with AsyncSessionLocal() as db:
        catalog_rows = (await db.execute(select(MetricCatalog))).scalars().all()
        existing = {
            r.metric_name
            for r in (await db.execute(select(MetricRule))).scalars().all()
        }

        inserted = 0
        skipped = 0
        for c in catalog_rows:
            if c.name in existing:
                skipped += 1
                continue
            algo, params = _choose_algorithm(c)
            rule = MetricRule(
                metric_name=c.name,
                rule_type="trend",
                algorithm=algo,
                params_json=json.dumps(params, ensure_ascii=False),
                verdict_map_json=None,
                enabled=True,
                updated_by="seed_script",
                description=f"auto-seeded from kind={c.kind} direction={c.direction}",
            )
            db.add(rule)
            inserted += 1
        await db.commit()

        return {"inserted": inserted, "skipped": skipped, "total_catalog": len(catalog_rows)}


async def main():
    result = await seed()
    logger.info(f"seed_metric_rules 完成: {result}")
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
