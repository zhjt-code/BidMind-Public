"""CPU 向量化路径并发加速比真模型微基准。

目标:
- 用真实 bge-m3 ONNX 模型, 不依赖任何 mock
- 横向比较 4 种方案在不同并发度下的真实 wall_time / speedup / efficiency
- 输出可复现的 JSON, 让方案选择基于数据而非想当然

四组对比:
    A. 串行 (concurrency=1, 单 session)            - 基线
    B. 共享 session, concurrency=2/4              - 当前架构 (encode_gate 单例 + 多协程)
    C. 4 路独立 EmbeddingModel ONNX 实例         - 候选修复方案 C
    D. (可选) ORT_PARALLEL execution_mode + inter_op_threads=2  - 候选修复方案 B

使用方式 (在 backend 目录下):
    ..\\.venv\\Scripts\\python.exe -m benchmarks.cpu_concurrency_bench

注意:
- 脚本会加载真实 bge-m3 模型 (~2GB), 首次约 5-10 秒模型加载
- 运行期间会消耗 CPU; 建议在 worker idle 时运行避免 OCR/LLM 干扰
- 结果落到 stdout, 同时保存 JSON 到 ./benchmarks/cpu_concurrency_bench_result.json
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


def _setup_env() -> None:
    """在 import torch / sentence_transformers 之前应用线程上限。"""
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from app.core.runtime.cpu_budget import (
        apply_env_thread_limits,
        get_cpu_budget,
    )

    budget = apply_env_thread_limits()
    print(f"[bench] CpuBudget: {budget.to_log_str()}")


_setup_env()


# 这些 import 必须在 _setup_env() 之后, 让 OMP/MKL 生效
from loguru import logger  # noqa: E402

logger.remove()
logger.add(sys.stderr, level="WARNING")  # 静音 INFO 减少噪声


def _make_texts(n_items: int, char_len: int) -> list[str]:
    """生成 n_items 条文本, 每条约 char_len 个字符。

    使用与生产文本中位数 (~200 字符) 接近的样本, 避免 padding 浪费失真。
    """
    sample = "本投标人按招标文件要求提供完整的资格证明材料,包括营业执照副本、税务登记证、组织机构代码证、安全生产许可证等所有相关证件的扫描件并加盖公章"
    base = (sample * (char_len // len(sample) + 1))[:char_len]
    return [base for _ in range(n_items)]


async def _encode_once(model, texts: list[str], batch_size: int = 16) -> float:
    t0 = time.perf_counter()
    await asyncio.to_thread(model.encode, texts, batch_size, False, None)
    return time.perf_counter() - t0


async def _measure_concurrency(
    models: list, texts: list[str], concurrency: int, batch_size: int = 16
) -> dict[str, float]:
    """跑 concurrency 路并发, 每路用 models[i % len(models)]; 返回 wall/avg_inner/speedup。

    Args:
        models: 1 个或多个模型实例; 若只有 1 个则所有协程共享 (方案 B);
                若有 N 个则每路独立 (方案 C)
    """
    if not models:
        raise ValueError("models 不能为空")

    # 串行单批基线 (用第一个 model 跑一次, 取均值)
    serial_runs = []
    for _ in range(2):
        serial_runs.append(await _encode_once(models[0], texts, batch_size))
    serial_avg = sum(serial_runs) / len(serial_runs)

    # 并发跑
    t0 = time.perf_counter()
    tasks = [
        _encode_once(models[i % len(models)], texts, batch_size)
        for i in range(concurrency)
    ]
    inner = await asyncio.gather(*tasks)
    wall = time.perf_counter() - t0
    avg_inner = sum(inner) / concurrency

    ideal = serial_avg * concurrency
    speedup = ideal / wall if wall > 0 else 0
    efficiency = speedup / concurrency if concurrency > 0 else 0

    return {
        "concurrency": concurrency,
        "n_models": len(models),
        "serial_avg_ms": round(serial_avg * 1000, 1),
        "wall_time_ms": round(wall * 1000, 1),
        "avg_inner_ms": round(avg_inner * 1000, 1),
        "ideal_serial_ms": round(ideal * 1000, 1),
        "speedup": round(speedup, 2),
        "efficiency_pct": round(efficiency * 100, 1),
    }


def _build_shared_model():
    """方案 A/B: 复用 encode_gate 单例 (生产代码当前路径)。"""
    from app.core.vectorizer.encode_gate import _get_cpu_fallback_model

    return _get_cpu_fallback_model()


def _build_independent_models(n: int):
    """方案 C: 构造 N 个独立的 EmbeddingModel(device='cpu')。

    每个实例有自己的 ONNX session + intra_op pool, 避免共享 session 串行化。
    """
    from app.core.vectorizer.embedding import EmbeddingModel

    models = []
    for i in range(n):
        print(f"[bench] 构建独立 model #{i + 1}/{n}...")
        m = EmbeddingModel(device="cpu")
        # 触发 ONNX session 加载
        _ = m.encode(["probe"], batch_size=1, show_progress=False)
        models.append(m)
    return models


async def main() -> None:
    n_items = 32
    char_len = 200
    texts = _make_texts(n_items, char_len)
    print(f"[bench] 测试样本: {n_items} items × {char_len} chars (贴近生产中位数)")

    results: list[dict[str, Any]] = []

    # ── 方案 A/B: 共享单 session ──
    print("\n[bench] === 方案 A/B: 共享单 ONNX session ===")
    print("[bench] 加载共享 session...")
    t0 = time.perf_counter()
    shared = _build_shared_model()
    print(f"[bench] shared session loaded in {time.perf_counter() - t0:.1f}s")

    # warmup
    print("[bench] warmup...")
    await _encode_once(shared, texts)

    for c in (1, 2, 4):
        print(f"[bench] 测 shared session, concurrency={c}...")
        r = await _measure_concurrency([shared], texts, c)
        r["scheme"] = "shared_session"
        results.append(r)
        print(
            f"  → wall={r['wall_time_ms']}ms, "
            f"speedup={r['speedup']}x, eff={r['efficiency_pct']}%"
        )

    # ── 方案 C: 4 路独立 session ──
    print("\n[bench] === 方案 C: 多 ONNX session 实例池 ===")
    independent = _build_independent_models(4)
    # warmup 各路
    print("[bench] warmup independent...")
    await asyncio.gather(*[_encode_once(m, texts) for m in independent])

    for c in (2, 4):
        print(f"[bench] 测 independent pool, concurrency={c}...")
        r = await _measure_concurrency(independent[:c], texts, c)
        r["scheme"] = "independent_pool"
        results.append(r)
        print(
            f"  → wall={r['wall_time_ms']}ms, "
            f"speedup={r['speedup']}x, eff={r['efficiency_pct']}%"
        )

    # ── 汇总 ──
    print("\n" + "=" * 70)
    print(f"{'scheme':<22}{'concurrency':>12}{'wall_ms':>12}{'speedup':>10}{'eff%':>8}")
    print("-" * 70)
    for r in results:
        print(
            f"{r['scheme']:<22}{r['concurrency']:>12}"
            f"{r['wall_time_ms']:>12}{r['speedup']:>10}{r['efficiency_pct']:>8}"
        )
    print("=" * 70)

    # 落 JSON
    out_path = Path(__file__).resolve().parent / "cpu_concurrency_bench_result.json"
    out_path.write_text(
        json.dumps(
            {
                "n_items": n_items,
                "char_len": char_len,
                "results": results,
                "platform": sys.platform,
                "intra_op_env": os.environ.get("OMP_NUM_THREADS"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n结果已写入: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
