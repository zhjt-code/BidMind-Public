"""部署后自动同步、校验并发布 LLM Center 代码注册源变更。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from loguru import logger


async def _auto_publish(username: str) -> dict[str, Any]:
    """在独立 DB 会话中完成自动发布事务。"""
    from app.core.database import AsyncSessionLocal
    from app.services.llm_center._helpers import _broadcast_invalidate
    from app.services.llm_center.auto_publish_service import (
        assert_auto_publish_success,
        auto_publish_code_registry,
    )

    async with AsyncSessionLocal() as db:
        try:
            report = await auto_publish_code_registry(db, username=username)
            assert_auto_publish_success(report)
            await db.commit()
            await _broadcast_invalidate(None)
            return report
        except Exception:
            await db.rollback()
            raise


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="Auto publish LLM Center code registry drafts after deployment.",
    )
    parser.add_argument(
        "--username",
        default="deploy:auto",
        help="Audit username used by validate/publish records.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI 入口，成功返回 0，失败返回 1。"""
    args = _parse_args(argv or sys.argv[1:])
    try:
        report = asyncio.run(_auto_publish(username=args.username))
    except Exception as exc:  # noqa: BLE001 - CLI 入口需统一转退出码
        logger.error(f"LLM Registry 自动发布失败: {exc}")
        return 1

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
