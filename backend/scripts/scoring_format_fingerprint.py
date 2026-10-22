"""生产版式指纹只读抽样（治理阶段 5；U2 盲区的最小验证实验）。

职责: 以**只读**方式统计生产库中评分表 chunk 的结构指纹分布，并与离线语料
      覆盖做差异对比，回答"现有 66 份语料对真实版式的代表性如何"。
不做: 不写任何表、不改任何落库字段、不记录正文原文、不参与业务裁决。

背景（U2）:
  技术圆桌四方一致承认的最大盲区: T0 基线保证"旧语料零漂移"，但对未入语料的
  生产版式无任何保证。方案的反转条件之一是"生产抽样显示现有语料缺失主要
  版式家族超 30%"——本脚本即该判据的取数工具。

隐私约束（项目规则 4.4）:
  指纹只包含**结构量**（列数序列、行数、是否有分值列/合并单元格、scope 数量），
  绝不写出单元格正文、表头文字或文档名。

用法（仓库根执行；需可访问生产/预发数据库）:
    .venv\\Scripts\\python.exe backend/scripts/scoring_format_fingerprint.py --limit 200
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import json
import os
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("LOG_LEVEL", "ERROR")
# --corpus-only 场景无需真实库；生产取数场景由 .env 提供真实 DATABASE_URL 覆盖
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
os.environ.setdefault("JWT_SECRET_KEY", "fingerprint-probe-key-not-for-production-use")
os.environ.setdefault("ADMIN_PASSWORD", "FingerprintProbe@Local1")

FIXTURE_ROOT = BACKEND_ROOT / "tests/fixtures/scoring"


def structural_fingerprint(payload: dict | None, content: str) -> dict:
    """从 chunk 载荷提取结构指纹（零正文）。"""
    grid = (payload or {}).get("grid") or []
    col_counts = [len(row) for row in grid]
    distinct_widths = sorted(set(col_counts))
    return {
        "row_count": len(grid),
        "distinct_col_widths": distinct_widths[:5],
        "width_unstable": len(distinct_widths) > 1,
        "has_column_roles": bool((payload or {}).get("column_roles")),
        "has_scope_rows": bool((payload or {}).get("scope_rows")),
        "has_lot": bool((payload or {}).get("lot")),
        "has_scheme": bool((payload or {}).get("scheme")),
        "content_len_bucket": min(len(content) // 2000, 9),
    }


def fingerprint_key(fp: dict) -> str:
    """把指纹压成可聚类的稳定短键。"""
    return (
        f"w{'-'.join(str(n) for n in fp['distinct_col_widths']) or 'na'}"
        f"|unstable={int(fp['width_unstable'])}"
        f"|roles={int(fp['has_column_roles'])}"
        f"|scope={int(fp['has_scope_rows'])}"
        f"|lot={int(fp['has_lot'])}"
        f"|scheme={int(fp['has_scheme'])}"
    )


def corpus_fingerprints() -> collections.Counter:
    """离线语料侧指纹分布（通过重建 chunk 得到，与生产同一提取路径）。"""
    from app.core.parser import ChunkPlanner
    from app.core.extractor.scoring.scoring_grid_payload import payload_from_chunk
    from app.core.parser.scoring_chunk_builder import build_scoring_table_chunks

    sys.path.insert(0, str(BACKEND_ROOT / "scripts"))
    from scoring_snapshot import _load_document, iter_fixtures  # noqa: E402

    counter: collections.Counter = collections.Counter()
    for _sample_id, path in iter_fixtures():
        document = _load_document(path)
        for chunk in build_scoring_table_chunks(document, ChunkPlanner(), 0):
            payload = payload_from_chunk(chunk)
            fp = structural_fingerprint(payload, getattr(chunk, "content", "") or "")
            counter[fingerprint_key(fp)] += 1
    return counter


async def production_fingerprints(limit: int) -> collections.Counter:
    """生产侧指纹分布（只读查询 chunks 表，不写不改）。"""
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.core.extractor.scoring.scoring_grid_payload import payload_from_chunk
    from app.models.chunk import Chunk

    counter: collections.Counter = collections.Counter()
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Chunk)
            .where(Chunk.chunk_type == "scoring_table")
            .order_by(Chunk.id.desc())
            .limit(limit)
        )
        for chunk in result.scalars().all():
            payload = payload_from_chunk(chunk)
            fp = structural_fingerprint(payload, getattr(chunk, "content", "") or "")
            counter[fingerprint_key(fp)] += 1
    return counter


def main() -> int:
    parser = argparse.ArgumentParser(description="生产评分版式指纹只读抽样")
    parser.add_argument("--limit", type=int, default=200, help="抽样 chunk 上限")
    parser.add_argument("--corpus-only", action="store_true", help="仅统计离线语料")
    args = parser.parse_args()

    corpus = corpus_fingerprints()
    report: dict = {
        "corpus_chunk_count": sum(corpus.values()),
        "corpus_distinct_shapes": len(corpus),
    }

    if not args.corpus_only:
        try:
            production = asyncio.run(production_fingerprints(args.limit))
        except Exception as exc:  # noqa: BLE001 取数失败必须可见, 不静默
            print(f"生产取数失败（可用 --corpus-only 跳过）: {exc}", file=sys.stderr)
            return 2
        uncovered = {k: v for k, v in production.items() if k not in corpus}
        covered_chunks = sum(v for k, v in production.items() if k in corpus)
        total = sum(production.values()) or 1
        report.update({
            "production_chunk_count": total,
            "production_distinct_shapes": len(production),
            "uncovered_shape_count": len(uncovered),
            "uncovered_chunk_ratio": round(1 - covered_chunks / total, 4),
            "top_uncovered_shapes": sorted(
                uncovered.items(), key=lambda kv: -kv[1]
            )[:10],
        })
        # 圆桌反转条件: 语料缺失主要版式家族超 30% 即需暂停治理计划并补样本
        report["reversal_condition_triggered"] = report["uncovered_chunk_ratio"] > 0.30

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
