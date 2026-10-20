r"""存量项目评分条款跨通道归并修复脚本。

职责: 对历史已提取项目执行 normal x scoring_item 跨通道归并收口, 消除确认页重复评分条款
 与下游重复预估(修复点已内置于提取流程, 本脚本用于回溯修复存量数据)。
不做: 不重新调用 LLM 提取、不改普通要求、不动已确认的 scoring_item。

默认 dry-run 仅报告; 加 --apply 才实际回填 normal 主行并删除冗余 scoring_item。

用法(PowerShell):
 .venv\Scripts\python.exe -m scripts.reconcile_scoring_items --project-id 1497
 .venv\Scripts\python.exe -m scripts.reconcile_scoring_items --project-id 1497 --apply
 .venv\Scripts\python.exe -m scripts.reconcile_scoring_items --all --apply
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict

from sqlalchemy import select

from app.core.database import async_session_factory
from app.models.requirement import Requirement
from app.core.extractor.scoring.scoring_reconcile import (
    _best_normal_match,
    _is_scoring_clause,
    bigram_similarity,
    reconcile_scoring_items,
)


async def _document_ids_for_project(db, project_id: int) -> list[int]:
    rows = (
        await db.execute(
            select(Requirement.document_id)
            .where(Requirement.project_id == project_id)
            .distinct()
        )
    ).scalars().all()
    return [d for d in rows if d is not None]


async def _all_document_ids_with_scoring(db) -> list[int]:
    rows = (
        await db.execute(
            select(Requirement.document_id)
            .where(Requirement.requirement_kind == "scoring_item")
            .distinct()
        )
    ).scalars().all()
    return [d for d in rows if d is not None]


async def _dry_run_report(db, document_id: int) -> int:
    rows = (
        await db.execute(
            select(Requirement)
            .where(Requirement.document_id == document_id)
            .order_by(Requirement.id)
        )
    ).scalars().all()
    normals = [
        r for r in rows
        if (getattr(r, "requirement_kind", "normal") or "normal") != "scoring_item"
        and _is_scoring_clause(r)
    ]
    scorings = [
        r for r in rows
        if (getattr(r, "requirement_kind", "normal") or "normal") == "scoring_item"
        and not bool(r.confirmed)
    ]
    would_merge = 0
    for s in scorings:
        match = _best_normal_match(s, normals)
        if match is not None:
            sim = bigram_similarity(match.description, s.description)
            print(
                f"  [dry-run] MERGE scoring#{s.id}(w={s.score_weight}) "
                f"-> normal#{match.id}(w={match.score_weight}) sim={sim:.3f}"
            )
            would_merge += 1
    print(
        f"  doc={document_id} normal_clauses={len(normals)} scoring_items={len(scorings)} "
        f"would_merge={would_merge}"
    )
    return would_merge


async def main() -> None:
    parser = argparse.ArgumentParser(description="评分条款跨通道归并修复")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--project-id", type=int, help="仅修复指定项目")
    group.add_argument("--all", action="store_true", help="修复所有含评分项的文档")
    parser.add_argument("--apply", action="store_true", help="实际执行(否则仅 dry-run)")
    args = parser.parse_args()

    async with async_session_factory() as db:
        if args.project_id is not None:
            doc_ids = await _document_ids_for_project(db, args.project_id)
        else:
            doc_ids = await _all_document_ids_with_scoring(db)

        if not doc_ids:
            print("未找到待处理文档")
            return

        total_merged = 0
        for doc_id in doc_ids:
            if args.apply:
                report = await reconcile_scoring_items(db, doc_id)
                print(
                    f"  doc={doc_id} merged={report.merged_count} "
                    f"examined_scoring={report.examined_scoring}"
                )
                total_merged += report.merged_count
            else:
                total_merged += await _dry_run_report(db, doc_id)

        mode = "已归并" if args.apply else "预计归并(dry-run)"
        print(f"完成: {mode} {total_merged} 条冗余 scoring_item, 涉及 {len(doc_ids)} 个文档")
        if not args.apply:
            print("提示: 加 --apply 实际执行")


if __name__ == "__main__":
    asyncio.run(main())
