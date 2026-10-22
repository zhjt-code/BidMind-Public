"""ARQ 运行时探针 / 本地双进程验收脚本

用途:
  - 校验 can_use_arq 判定结果: 开关/Redis/worker 心跳三条件是否同时满足
  - 列出当前新鲜窗口内的存活 worker 列表
  - 统计 ARQ 队列当前深度 (ARQ 使用 zset)
  - 在 worker 下线场景下, 预期看到 has_alive_worker=False 并触发回退

使用:
  cd backend
  python scripts/arq_runtime_probe.py

  # 另开一个终端启动 worker:
  cd backend
  arq app.worker.WorkerSettings

  # 再次跑探针, 应看到 workers 列表非空、can_use_arq=True (如果 VECTOR_PREFER_ARQ=true)
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.arq_runtime import (  # noqa: E402
    can_use_arq,
    has_alive_worker_for,
    scan_alive_workers_sync,
)
from app.core.config import settings  # noqa: E402
from app.core.redis import get_redis_pool, get_sync_redis  # noqa: E402
from app.core.tasks.resource_permit import (  # noqa: E402
    get_resource_owners_sync,
    get_resource_snapshots_sync,
)
from app.core.tasks.resource_policy import all_resource_queue_names  # noqa: E402


def _queue_size(client, key: str) -> tuple[str, int, list[str]]:
    """返回 Redis 队列类型、长度和少量 job id。"""

    key_type = client.type(key)
    if key_type == "zset":
        return key_type, int(client.zcard(key) or 0), list(client.zrange(key, 0, 9) or [])
    if key_type == "list":
        return key_type, int(client.llen(key) or 0), list(client.lrange(key, 0, 9) or [])
    if key_type in {"none", None}:
        return "none", 0, []
    return str(key_type), int(client.exists(key) or 0), []


async def _query_queue_depth() -> dict[str, dict]:
    """统计 ARQ 队列状态, 失败返回 {}。"""
    client = get_sync_redis()
    if client is None:
        return {}
    result: dict[str, dict] = {}
    try:
        # ARQ 默认队列 + 可选资源队列
        for key in all_resource_queue_names():
            try:
                key_type, depth, sample = _queue_size(client, key)
            except Exception as exc:
                key_type, depth, sample = "error", -1, [str(exc)]
            result[key] = {"type": key_type, "depth": depth, "sample": sample}
    except Exception:
        pass
    return result


async def main() -> int:
    print("=" * 60)
    print("ARQ 运行时探针")
    print("=" * 60)

    # 1) 运维开关
    prefer_arq = bool(getattr(settings, "VECTOR_PREFER_ARQ", False))
    print(f"[1] VECTOR_PREFER_ARQ = {prefer_arq}")

    # 2) Redis 连通性
    sync_client = get_sync_redis()
    print(f"[2] Redis 同步连通: {sync_client is not None}")
    pool = await get_redis_pool()
    print(f"    Redis ARQ 异步池: {pool is not None}")

    # 3) worker 心跳
    workers = scan_alive_workers_sync()
    now = int(time.time())
    print(f"[3] 存活 worker 数: {len(workers)}")
    for w in workers:
        funcs = w.get("functions") or []
        last_seen = int(w.get("last_seen") or 0)
        print(
            f"    - worker_id={w.get('worker_id')}, host={w.get('host')}, "
            f"pid={w.get('pid')}, last_seen={last_seen}, age={now - last_seen}s, "
            f"state={w.get('state', 'unknown')}, current={w.get('current_function')}, "
            f"lag_ms={w.get('event_loop_lag_ms')}, functions={funcs}"
        )

    # 4) 每个主任务的消费者判定
    for fn in (
        "process_document_job",
        "extract_requirements_job",
        "review_project_job",
        "collusion_analyze_job",
        "writing_material_process_job",
        "writing_compose_job",
    ):
        ok = has_alive_worker_for(fn)
        print(f"    has_alive_worker_for({fn}) = {ok}")

    # 5) 三条件综合判定
    can = await can_use_arq("process_document_job")
    print(f"[4] can_use_arq('process_document_job') = {can}")

    # 6) 队列深度 (可选)
    depth = await _query_queue_depth()
    print(f"[5] 队列深度: {json.dumps(depth, ensure_ascii=False)}")

    snapshots = [
        {
            "resource_type": item.resource_type,
            "inflight": item.inflight,
            "limit": item.limit,
            "owners": get_resource_owners_sync(item.resource_type),
        }
        for item in get_resource_snapshots_sync()
    ]
    print(f"[6] 资源许可: {json.dumps(snapshots, ensure_ascii=False)}")

    print("=" * 60)
    if prefer_arq and pool is not None and workers:
        print("结论: ARQ 通道三条件已满足, 可以走 ARQ。")
        return 0
    if not prefer_arq:
        print("结论: VECTOR_PREFER_ARQ=false, API 将始终回退 asyncio。")
        return 0
    if pool is None:
        print("结论: Redis 不可用, API 将回退 asyncio。请确认 docker compose up -d redis。")
        return 1
    if not workers:
        print(
            "结论: 未检测到新鲜 worker 心跳, API 将回退 asyncio。\n"
            "请在另一终端执行: cd backend && arq app.worker.WorkerSettings"
        )
        return 1
    print("结论: 其它原因不满足, 请检查日志。")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
