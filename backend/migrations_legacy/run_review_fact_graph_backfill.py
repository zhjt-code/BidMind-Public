"""事实图回填脚本 - 运行方式: python migrations/run_review_fact_graph_backfill.py"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

script_dir = Path(__file__).resolve().parent
backend_dir = script_dir.parent
sys.path.insert(0, str(backend_dir))
os.chdir(backend_dir)

from dotenv import load_dotenv
from sqlalchemy import select

from app.core.ai.reviewer import get_review_orchestrator
from app.core.database import async_session_factory
from app.models.review_result import ReviewResult

load_dotenv()


async def run_backfill() -> None:
    # 先确保事实图结构存在
    subprocess.run(
        [sys.executable, str(script_dir / "run_review_fact_graph_v1_migration.py")],
        check=True,
    )

    async with async_session_factory() as session:
        project_ids = sorted(
            {
                row[0]
                for row in (await session.execute(select(ReviewResult.project_id))).all()
                if row and row[0] is not None
            }
        )

    async with async_session_factory() as session:
        orchestrator = get_review_orchestrator(session)
        for project_id in project_ids:
            await orchestrator._post_review_consistency_check(project_id)  # noqa: SLF001
            print(f"fact-graph reconciled project={project_id}")

    print(f"fact-graph backfill done, projects={len(project_ids)}")


if __name__ == "__main__":
    asyncio.run(run_backfill())
