"""Export writer quality baseline assets.

职责: 提供写标质量基线导出的命令行入口。
不做: 不在命令行接收密钥明文、不提交或移动导出结果。
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def _read_secret_from_env() -> bytes:
    raw = os.environ.get("WRITER_REGRESSION_HMAC_KEY", "").strip()
    if not raw:
        raise SystemExit("WRITER_REGRESSION_HMAC_KEY is required")
    try:
        secret = bytes.fromhex(raw)
    except ValueError as exc:
        raise SystemExit("WRITER_REGRESSION_HMAC_KEY must be hex") from exc
    if len(secret) < 32:
        raise SystemExit("WRITER_REGRESSION_HMAC_KEY must be at least 32 bytes")
    return secret


def main() -> int:
    parser = argparse.ArgumentParser(description="Export writer quality baseline")
    sub = parser.add_subparsers(dest="command", required=True)
    collect = sub.add_parser("collect")
    collect.add_argument("--limit", type=int, default=15)
    collect.add_argument("--max-sections-per-project", type=int, default=12)
    collect.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    _read_secret_from_env()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    marker = output_dir / "README.txt"
    marker.write_text(
        "Writer quality baseline export directory. "
        "Implementation loads eligible projects in the service layer.\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
