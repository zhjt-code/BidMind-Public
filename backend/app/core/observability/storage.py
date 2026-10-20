"""时序存储: Redis ZSet 热层 + metric_snapshots 温层 + 定时 flush.

热层 key 结构:
    metrics:ts:{name}                 -> sorted set, score=bucket_ts, value=JSON(agg)
    metrics:ts:recent                 -> sorted set of all active {name} (用于 flush 时遍历)

温层表: metric_snapshots (name, labels_hash, bucket_ts, count, sum, p50, p95)

flush 策略: ARQ 周期任务 flush_metric_snapshots (每 10min)
    - 遍历 metrics:ts:recent, 对每个 name 读出 >= 上次 flush ts 的 bucket
    - 聚合后写入 metric_snapshots
    - 清理 72h 之前的 ZSet 成员
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger


HOT_KEY_PREFIX = "metrics:ts:"
RECENT_NAMES_KEY = "metrics:ts:recent"
DEFAULT_BUCKET_SECONDS = 300  # 5min
HOT_RETENTION_SECONDS = 72 * 3600  # 72h


def labels_hash(labels: dict[str, str]) -> str:
    """将 label dict 规范化成短哈希."""
    normalized = json.dumps(labels, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()[:12]


def _bucket_ts(ts: Optional[float] = None, bucket_seconds: int = DEFAULT_BUCKET_SECONDS) -> int:
    t = ts if ts is not None else time.time()
    return int(t // bucket_seconds * bucket_seconds)


async def record_sample(
    *,
    name: str,
    value: float,
    labels: Optional[dict[str, str]] = None,
    bucket_seconds: int = DEFAULT_BUCKET_SECONDS,
) -> None:
    """写入一个采样点到 Redis ZSet 热层. 失败静默."""
    try:
        from app.core.redis import get_redis_pool
        pool = await get_redis_pool()
        if pool is None:
            return

        lh = labels_hash(labels or {})
        bucket = _bucket_ts(bucket_seconds=bucket_seconds)
        key = f"{HOT_KEY_PREFIX}{name}:{lh}"

        # 读出当前 bucket 的聚合 (简化: 用 HGETALL + INCR 方案)
        hkey = f"{HOT_KEY_PREFIX}{name}:{lh}:{bucket}"
        pipe = pool.pipeline()
        pipe.hincrby(hkey, "count", 1)
        pipe.hincrbyfloat(hkey, "sum", value)
        pipe.expire(hkey, HOT_RETENTION_SECONDS)
        # 记录这个 bucket 存在 (用于 flush 时遍历)
        pipe.zadd(key, {str(bucket): bucket})
        pipe.zremrangebyscore(key, 0, bucket - HOT_RETENTION_SECONDS)
        pipe.expire(key, HOT_RETENTION_SECONDS)
        # 记录 name 存在 (flush 任务遍历入口)
        pipe.sadd(RECENT_NAMES_KEY, f"{name}:{lh}")
        await pipe.execute()
    except Exception as e:
        logger.debug(f"record_sample[{name}] 失败 (忽略): {e}")


async def query_timeseries(
    *,
    name: str,
    labels: Optional[dict[str, str]] = None,
    window_seconds: int = 24 * 3600,
    bucket_seconds: int = DEFAULT_BUCKET_SECONDS,
) -> list[dict[str, Any]]:
    """读取指定指标 + label 组合在近窗口的时间序列."""
    try:
        from app.core.redis import get_redis_pool
        pool = await get_redis_pool()
        if pool is None:
            return []

        lh = labels_hash(labels or {})
        key = f"{HOT_KEY_PREFIX}{name}:{lh}"
        now_bucket = _bucket_ts(bucket_seconds=bucket_seconds)
        start_bucket = now_bucket - window_seconds

        buckets = await pool.zrangebyscore(key, start_bucket, now_bucket)
        result: list[dict[str, Any]] = []
        for b in buckets:
            if isinstance(b, bytes):
                b_str = b.decode("utf-8")
            else:
                b_str = str(b)
            hkey = f"{HOT_KEY_PREFIX}{name}:{lh}:{b_str}"
            hashed = await pool.hgetall(hkey)
            if not hashed:
                continue
            cnt = float(hashed.get("count", 0) or hashed.get(b"count", 0) or 0)
            sm = float(hashed.get("sum", 0) or hashed.get(b"sum", 0) or 0)
            result.append({
                "ts": datetime.fromtimestamp(int(b_str), tz=timezone.utc).isoformat(),
                "bucket_ts": int(b_str),
                "count": cnt,
                "sum": sm,
                "avg": (sm / cnt) if cnt else 0.0,
            })
        return result
    except Exception as e:
        logger.debug(f"query_timeseries[{name}] 失败 (忽略): {e}")
        return []


async def flush_to_snapshots() -> dict[str, int]:
    """把 Redis 热层数据滚动聚合到 metric_snapshots 表.

    设计: 每 10min 调用一次, 每次 flush "上一个完整 bucket" 的数据.
    返回 {"flushed": N, "skipped": M}.
    """
    from app.core.database import AsyncSessionLocal
    from app.core.redis import get_redis_pool
    from app.models.metric_snapshot import MetricSnapshot

    pool = await get_redis_pool()
    if pool is None:
        return {"flushed": 0, "skipped": 0}

    try:
        raw_members = await pool.smembers(RECENT_NAMES_KEY)
    except Exception as e:
        logger.debug(f"flush_to_snapshots smembers 失败: {e}")
        return {"flushed": 0, "skipped": 0}

    members: list[str] = []
    for m in raw_members:
        if isinstance(m, bytes):
            members.append(m.decode("utf-8"))
        else:
            members.append(str(m))

    flushed = 0
    skipped = 0
    now_bucket = _bucket_ts()
    prev_bucket = now_bucket - DEFAULT_BUCKET_SECONDS

    async with AsyncSessionLocal() as db:
        for member in members:
            try:
                if ":" not in member:
                    continue
                name, lh = member.rsplit(":", 1)
                hkey = f"{HOT_KEY_PREFIX}{name}:{lh}:{prev_bucket}"
                hashed = await pool.hgetall(hkey)
                if not hashed:
                    skipped += 1
                    continue
                cnt = float(hashed.get("count", 0) or hashed.get(b"count", 0) or 0)
                sm = float(hashed.get("sum", 0) or hashed.get(b"sum", 0) or 0)
                snap = MetricSnapshot(
                    name=name,
                    labels_hash=lh,
                    bucket_ts=datetime.fromtimestamp(prev_bucket, tz=timezone.utc),
                    bucket_seconds=DEFAULT_BUCKET_SECONDS,
                    count=int(cnt),
                    sum=sm,
                    avg=(sm / cnt) if cnt else 0.0,
                )
                db.add(snap)
                flushed += 1
            except Exception as e:
                logger.debug(f"flush member {member} 失败: {e}")
                continue
        try:
            await db.commit()
        except Exception as e:
            logger.warning(f"flush_to_snapshots commit 失败: {e}")
            await db.rollback()
    return {"flushed": flushed, "skipped": skipped}
