"""目录页码 backfill 脚本安全护栏测试。"""

import os
import subprocess
import sys


def test_backfill_apply_requires_scope() -> None:
    """禁止无 project/document 过滤条件的全库 apply。"""
    env = os.environ.copy()
    env.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    env.setdefault("JWT_SECRET_KEY", "test-secret")
    env.setdefault("ADMIN_PASSWORD", "test-password")

    result = subprocess.run(
        [
            sys.executable,
            "backend/scripts/backfill_toc_coordinates.py",
            "--apply",
        ],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "--apply 必须指定 --project-id 或 --document-id" in combined
