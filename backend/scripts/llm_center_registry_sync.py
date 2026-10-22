"""LLM Center 配置表导出/导入（本地→云端全量对齐）。

职责: 导出/导入 LLM Center 配置表，使云端与本地 UI 配置面 100% 一致。
不做: 业务审查、模型池(ai_settings)同步、execution_traces 迁移、代码常量回写。
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import re
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine


FORMAT_VERSION = 1
DEFAULT_DUMP_NAME = "llm_center_registry.json.gz"

# 父→子插入顺序（export / import insert）
TABLES_INSERT_ORDER: tuple[str, ...] = (
    "llm_flows",
    "llm_stages",
    "llm_nodes",
    "llm_template_versions",
    "llm_param_defs",
    "llm_node_edges",
    "llm_publish_records",
)

# 子→父清空顺序（import truncate）
TABLES_TRUNCATE_ORDER: tuple[str, ...] = tuple(reversed(TABLES_INSERT_ORDER))

EXCLUDED_TABLES: tuple[str, ...] = ("llm_execution_traces",)

NOISE_PATTERNS: tuple[str, ...] = (
    "<!-- batch-pub-test -->",
    "<!-- rollback source -->",
)

_NOISE_RE = re.compile(
    "|".join(re.escape(p) for p in NOISE_PATTERNS),
    re.IGNORECASE,
)


class LlmCenterRegistrySyncError(RuntimeError):
    """LLM Center 配置同步失败。"""


def _load_database_url_from_env_file(env_path: Path) -> str | None:
    """从 .env 读取 DATABASE_URL（deploy 脚本本地导出用）。"""
    if not env_path.is_file():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() == "DATABASE_URL":
            return value.strip().strip('"').strip("'")
    return None


def resolve_database_url(explicit: str | None = None) -> str:
    """解析 DATABASE_URL：显式参数 > 环境变量 > backend/.env。"""
    import os

    if explicit:
        return explicit
    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        return env_url
    # scripts/ 位于 backend/scripts/
    backend_root = Path(__file__).resolve().parents[1]
    from_file = _load_database_url_from_env_file(backend_root / ".env")
    if from_file:
        return from_file
    raise LlmCenterRegistrySyncError(
        "未找到 DATABASE_URL（请设置环境变量或提供 backend/.env）"
    )


_PG_TIMESTAMPTZ_TYPES = frozenset({"timestamp with time zone", "timestamptz"})
_PG_TIMESTAMP_TYPES = frozenset({"timestamp without time zone", "timestamp"})
_PG_DATE_TYPES = frozenset({"date"})


def coerce_import_value(value: Any, pg_data_type: str) -> Any:
    """按 PG 列类型还原 JSON 快照中的绑定值。

    职责: 把 timestamptz/timestamp/date 的 ISO 字符串还原为 datetime/date。
    不做: 不根据字符串形态猜测类型，不改 text/json 列内容。
    """
    if value is None:
        return value
    kind = (pg_data_type or "").strip().lower()
    if kind in _PG_TIMESTAMPTZ_TYPES or kind in _PG_TIMESTAMP_TYPES:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value
    if kind in _PG_DATE_TYPES:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            return date.fromisoformat(value[:10])
        return value
    return value


def _json_default(value: Any) -> Any:
    """JSON 序列化扩展类型。"""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, memoryview)):
        return bytes(value).hex()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _parse_cell(value: Any, type_code: Any) -> Any:
    """将驱动返回值规范为可往返的 Python 对象。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return value


def ensure_snapshot_columns_exist(
    snapshot_tables: dict[str, Any],
    live_columns: dict[str, list[str]],
) -> None:
    """
    职责: 确认快照中每张 llm_* 表的列在目标库都存在。
    不做: 不比较全局 alembic revision，不写入数据。
    """
    for table in TABLES_INSERT_ORDER:
        data = snapshot_tables.get(table)
        if not isinstance(data, dict):
            raise LlmCenterRegistrySyncError(f"快照缺少表: {table}")
        snap_cols = [str(c) for c in (data.get("columns") or [])]
        if not snap_cols:
            raise LlmCenterRegistrySyncError(f"快照表缺少列清单: {table}")
        live_cols = live_columns.get(table)
        if not live_cols:
            raise LlmCenterRegistrySyncError(f"目标库缺少表: {table}")
        live_set = set(live_cols)
        missing = [c for c in snap_cols if c not in live_set]
        if missing:
            raise LlmCenterRegistrySyncError(
                f"表 {table} 目标库缺少列 {missing}，拒绝导入"
            )


async def _fetch_alembic_version(conn: AsyncConnection) -> str | None:
    """读取 alembic_version.version_num。"""
    result = await conn.execute(
        text(
            "SELECT version_num FROM alembic_version "
            "ORDER BY version_num LIMIT 1"
        )
    )
    row = result.first()
    return str(row[0]) if row else None


async def _table_columns(conn: AsyncConnection, table: str) -> list[str]:
    """按 ordinal_position 返回表列名。"""
    result = await conn.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :table
            ORDER BY ordinal_position
            """
        ),
        {"table": table},
    )
    cols = [str(r[0]) for r in result.fetchall()]
    if not cols:
        raise LlmCenterRegistrySyncError(f"表不存在或无列: {table}")
    return cols


async def _table_column_types(conn: AsyncConnection, table: str) -> dict[str, str]:
    """返回 {column_name: information_schema.data_type}。"""
    result = await conn.execute(
        text(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :table
            """
        ),
        {"table": table},
    )
    types = {str(r[0]): str(r[1]) for r in result.fetchall()}
    if not types:
        raise LlmCenterRegistrySyncError(f"表不存在或无列: {table}")
    return types


async def _export_table(
    conn: AsyncConnection, table: str
) -> dict[str, Any]:
    """导出单表全部行。"""
    columns = await _table_columns(conn, table)
    col_list = ", ".join(f'"{c}"' for c in columns)
    result = await conn.execute(text(f"SELECT {col_list} FROM {table} ORDER BY id"))
    rows: list[list[Any]] = []
    for row in result.fetchall():
        rows.append([_parse_cell(v, None) for v in row])
    return {"columns": columns, "rows": rows, "row_count": len(rows)}


async def _sequence_name(conn: AsyncConnection, table: str) -> str | None:
    """解析表 id 列关联的 sequence（若有）。"""
    result = await conn.execute(
        text("SELECT pg_get_serial_sequence(:table, 'id')"),
        {"table": table},
    )
    row = result.first()
    if not row or row[0] is None:
        return None
    return str(row[0])


async def _export_sequences(
    conn: AsyncConnection, tables: tuple[str, ...]
) -> dict[str, int]:
    """导出各表 id sequence 当前值。"""
    sequences: dict[str, int] = {}
    for table in tables:
        seq = await _sequence_name(conn, table)
        if not seq:
            continue
        result = await conn.execute(text(f"SELECT last_value FROM {seq}"))
        row = result.first()
        if row is not None:
            sequences[seq] = int(row[0])
    return sequences


def scan_noise_in_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """扫描快照中 active/任意 content 的测试噪音标记。"""
    hits: list[dict[str, Any]] = []
    versions = payload.get("tables", {}).get("llm_template_versions")
    if not versions:
        return hits
    columns: list[str] = versions["columns"]
    try:
        content_idx = columns.index("content")
        id_idx = columns.index("id")
        active_idx = columns.index("is_active") if "is_active" in columns else None
    except ValueError:
        return hits
    for row in versions["rows"]:
        content = row[content_idx] if content_idx < len(row) else None
        if not isinstance(content, str) or not _NOISE_RE.search(content):
            continue
        hits.append(
            {
                "table": "llm_template_versions",
                "id": row[id_idx] if id_idx < len(row) else None,
                "is_active": row[active_idx] if active_idx is not None else None,
                "markers": sorted(set(_NOISE_RE.findall(content))),
            }
        )
    return hits


async def export_registry(engine: AsyncEngine) -> dict[str, Any]:
    """从当前库导出 LLM Center 配置快照。"""
    async with engine.connect() as conn:
        alembic_version = await _fetch_alembic_version(conn)
        tables: dict[str, Any] = {}
        for table in TABLES_INSERT_ORDER:
            tables[table] = await _export_table(conn, table)
            logger.info(
                f"[LLM Center export] {table}: {tables[table]['row_count']} rows"
            )
        sequences = await _export_sequences(conn, TABLES_INSERT_ORDER)

    payload = {
        "format_version": FORMAT_VERSION,
        "kind": "llm_center_registry",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "alembic_version": alembic_version,
        "tables": tables,
        "sequences": sequences,
        "excluded_tables": list(EXCLUDED_TABLES),
    }
    return payload


def write_payload(payload: dict[str, Any], output: Path) -> None:
    """写入 gzip JSON 快照。"""
    output.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, default=_json_default, indent=2)
    with gzip.open(output, "wt", encoding="utf-8") as fh:
        fh.write(raw)


def read_payload(input_path: Path) -> dict[str, Any]:
    """读取 gzip 或明文 JSON 快照。"""
    if not input_path.is_file():
        raise LlmCenterRegistrySyncError(f"快照文件不存在: {input_path}")
    raw_bytes = input_path.read_bytes()
    if input_path.suffix == ".gz" or raw_bytes[:2] == b"\x1f\x8b":
        text_body = gzip.decompress(raw_bytes).decode("utf-8")
    else:
        text_body = raw_bytes.decode("utf-8")
    payload = json.loads(text_body)
    if payload.get("kind") != "llm_center_registry":
        raise LlmCenterRegistrySyncError("快照 kind 不是 llm_center_registry")
    if int(payload.get("format_version") or 0) != FORMAT_VERSION:
        raise LlmCenterRegistrySyncError(
            f"不支持的 format_version={payload.get('format_version')}"
        )
    return payload


async def _set_sequence(
    conn: AsyncConnection, seq: str, last_value: int
) -> None:
    """将 sequence 重置到至少 last_value（下次 nextval = last_value+1 若 is_called）。"""
    await conn.execute(
        text("SELECT setval(CAST(:seq AS regclass), :val, true)"),
        {"seq": seq, "val": max(int(last_value), 1)},
    )


async def import_registry(
    engine: AsyncEngine,
    payload: dict[str, Any],
    *,
    yes: bool,
    backup_path: Path | None,
) -> dict[str, Any]:
    """将快照导入当前库（覆盖目标表）。"""
    async with engine.connect() as conn:
        local_rev = await _fetch_alembic_version(conn)
        snap_rev = payload.get("alembic_version")
        summary = {
            "local_alembic_version": local_rev,
            "snapshot_alembic_version": snap_rev,
            "tables": {
                name: data.get("row_count", len(data.get("rows", [])))
                for name, data in payload.get("tables", {}).items()
            },
            "noise_hits": scan_noise_in_payload(payload),
        }

        if snap_rev and local_rev and snap_rev != local_rev:
            logger.warning(
                f"Alembic 版本不同: snapshot={snap_rev} local={local_rev}；"
                "仅校验 llm_* 列是否存在，不阻止导入"
            )
        live_columns = {
            table: await _table_columns(conn, table)
            for table in TABLES_INSERT_ORDER
        }
        ensure_snapshot_columns_exist(payload.get("tables") or {}, live_columns)

        if not yes:
            logger.warning("dry-run：未传 --yes，不写入数据库")
            return {"ok": True, "dry_run": True, "summary": summary}

        if backup_path is not None:
            backup_payload = await export_registry(engine)
            write_payload(backup_payload, backup_path)
            logger.info(f"[LLM Center import] 云端备份已写入 {backup_path}")

    async with engine.begin() as conn:
        for table in TABLES_TRUNCATE_ORDER:
            await conn.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
            logger.info(f"[LLM Center import] truncated {table}")

        for table in TABLES_INSERT_ORDER:
            data = payload["tables"].get(table)
            if not data:
                raise LlmCenterRegistrySyncError(f"快照缺少表: {table}")
            columns: list[str] = data["columns"]
            rows: list[list[Any]] = data["rows"]
            if not rows:
                continue
            col_types = await _table_column_types(conn, table)
            col_list = ", ".join(f'"{c}"' for c in columns)
            placeholders = ", ".join(f":c{i}" for i in range(len(columns)))
            sql = text(
                f'INSERT INTO {table} ({col_list}) VALUES ({placeholders})'
            )
            for row in rows:
                params = {
                    f"c{i}": coerce_import_value(
                        row[i], col_types.get(columns[i], "")
                    )
                    for i in range(len(columns))
                }
                await conn.execute(sql, params)
            logger.info(f"[LLM Center import] inserted {table}: {len(rows)} rows")

        for seq, last_value in (payload.get("sequences") or {}).items():
            await _set_sequence(conn, str(seq), int(last_value))
            logger.info(f"[LLM Center import] setval {seq}={last_value}")

    return {"ok": True, "dry_run": False, "summary": summary, "backup": str(backup_path) if backup_path else None}


async def _broadcast_after_import() -> None:
    """导入后广播 PromptRegistry 失效。"""
    try:
        from app.services.llm_center._helpers import _broadcast_invalidate

        await _broadcast_invalidate(None)
        logger.info("[LLM Center import] 已广播 prompt_registry invalidate")
    except Exception as exc:  # noqa: BLE001 - 导入成功后缓存刷新失败不应掩盖主结果
        logger.warning(f"[LLM Center import] 广播 invalidate 失败（库已写入）: {exc}")


def _build_parser() -> argparse.ArgumentParser:
    """构建 CLI。"""
    parser = argparse.ArgumentParser(
        description="Export/import LLM Center registry tables for local→cloud parity.",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Override DATABASE_URL (default: env or backend/.env).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    export_p = sub.add_parser("export", help="Export registry snapshot")
    export_p.add_argument(
        "--output",
        type=Path,
        default=None,
        help=f"Output path (default: backend/storage/{DEFAULT_DUMP_NAME})",
    )

    import_p = sub.add_parser("import", help="Import registry snapshot (overwrite)")
    import_p.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Snapshot path (.json.gz)",
    )
    import_p.add_argument(
        "--yes",
        action="store_true",
        help="Actually write DB (default is dry-run)",
    )
    import_p.add_argument(
        "--backup",
        type=Path,
        default=None,
        help="Backup path before overwrite (default: storage/llm_center_registry.bak.<ts>.json.gz)",
    )
    import_p.add_argument(
        "--skip-backup",
        action="store_true",
        help="Skip automatic backup (not recommended)",
    )

    pre_p = sub.add_parser("preflight", help="Scan snapshot or live DB for noise markers")
    pre_p.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Optional snapshot path; if omitted, export live DB in-memory and scan",
    )
    return parser


async def _async_main(args: argparse.Namespace) -> dict[str, Any]:
    """异步主流程。"""
    url = resolve_database_url(args.database_url)
    engine = create_async_engine(url, pool_pre_ping=True)
    backend_storage = Path(__file__).resolve().parents[1] / "storage"

    try:
        if args.command == "export":
            output = args.output or (backend_storage / DEFAULT_DUMP_NAME)
            payload = await export_registry(engine)
            noise = scan_noise_in_payload(payload)
            if noise:
                logger.warning(
                    f"[LLM Center export] 检测到 {len(noise)} 条含测试噪音标记的模板版本 "
                    f"(仍将导出以保证 100% 原样): {noise[:5]}"
                )
            write_payload(payload, output)
            return {
                "ok": True,
                "command": "export",
                "output": str(output),
                "alembic_version": payload.get("alembic_version"),
                "row_counts": {
                    k: v["row_count"] for k, v in payload["tables"].items()
                },
                "noise_hits": noise,
            }

        if args.command == "preflight":
            if args.input:
                payload = read_payload(args.input)
            else:
                payload = await export_registry(engine)
            noise = scan_noise_in_payload(payload)
            return {
                "ok": True,
                "command": "preflight",
                "noise_hits": noise,
                "alembic_version": payload.get("alembic_version"),
                "tables": list(TABLES_INSERT_ORDER),
                "excluded_tables": list(EXCLUDED_TABLES),
            }

        if args.command == "import":
            payload = read_payload(args.input)
            noise = scan_noise_in_payload(payload)
            if noise:
                logger.warning(
                    f"[LLM Center import] 快照含 {len(noise)} 条噪音标记模板，将按 100% 原样写入"
                )
            backup_path: Path | None = None
            if args.yes and not args.skip_backup:
                ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
                backup_path = args.backup or (
                    backend_storage / f"llm_center_registry.bak.{ts}.json.gz"
                )
            report = await import_registry(
                engine, payload, yes=args.yes, backup_path=backup_path
            )
            if args.yes and report.get("ok") and not report.get("dry_run"):
                await _broadcast_after_import()
            report["command"] = "import"
            report["noise_hits"] = noise
            return report

        raise LlmCenterRegistrySyncError(f"未知命令: {args.command}")
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。"""
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(_async_main(args))
    except LlmCenterRegistrySyncError as exc:
        logger.error(str(exc))
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"LLM Center registry sync failed: {exc}")
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps(report, ensure_ascii=False, indent=2, default=_json_default))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
