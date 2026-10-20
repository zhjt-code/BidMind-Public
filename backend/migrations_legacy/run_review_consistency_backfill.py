"""一致性根治修复回填脚本 - 运行方式: python migrations/run_review_consistency_backfill.py"""

import asyncio
import os
import sys

script_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(script_dir)
sys.path.insert(0, backend_dir)
os.chdir(backend_dir)

from dotenv import load_dotenv
from sqlalchemy import select

from app.core.ai.reviewer import get_review_orchestrator
from app.core.database import async_session_factory
from app.models.requirement import Requirement
from app.models.review_result import ReviewResult

load_dotenv()


def _normalize_fact_key(raw: str | None) -> str | None:
    if not raw:
        return None
    # 旧格式: topic:digest:scope -> 新格式: topic:digest
    parts = raw.split(":")
    if len(parts) >= 2:
        return f"{parts[0]}:{parts[1]}"
    return raw


async def run_backfill() -> None:
    async with async_session_factory() as session:
        req_rows = (await session.execute(select(Requirement))).scalars().all()
        for req in req_rows:
            req.canonical_key = _normalize_fact_key(req.canonical_key)
        await session.commit()

        rr_rows = (await session.execute(select(ReviewResult))).scalars().all()
        touched_projects: set[int] = set()
        for rr in rr_rows:
            normalized = _normalize_fact_key(rr.fact_key or rr.canonical_key)
            rr.fact_key = normalized
            rr.canonical_key = normalized or rr.canonical_key
            if rr.project_id:
                touched_projects.add(rr.project_id)
        await session.commit()

    # 项目级可重入收敛
    async with async_session_factory() as session:
        orchestrator = get_review_orchestrator(session)
        for project_id in sorted(touched_projects):
            await orchestrator._post_review_consistency_check(project_id)  # noqa: SLF001
            print(f"reconciled project={project_id}")

    print(f"backfill done, projects={len(touched_projects)}")


if __name__ == "__main__":
    asyncio.run(run_backfill())
