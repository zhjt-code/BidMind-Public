"""LLM Center 配置同步脚本契约测试。

锁定表清单、排除 traces、噪音扫描与快照读写往返。
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import llm_center_registry_sync as sync


def test_table_lists_exclude_traces_and_cover_required_config_tables() -> None:
    """纳入表必须覆盖配置面，且不得包含 execution_traces。"""
    required = {
        "llm_flows",
        "llm_stages",
        "llm_nodes",
        "llm_template_versions",
        "llm_param_defs",
        "llm_node_edges",
        "llm_publish_records",
    }
    assert set(sync.TABLES_INSERT_ORDER) == required
    assert sync.TABLES_TRUNCATE_ORDER == tuple(reversed(sync.TABLES_INSERT_ORDER))
    assert "llm_execution_traces" in sync.EXCLUDED_TABLES
    assert "llm_execution_traces" not in sync.TABLES_INSERT_ORDER


def test_scan_noise_detects_batch_pub_and_rollback_markers() -> None:
    """噪音扫描必须命中 batch-pub-test / rollback source。"""
    payload = {
        "tables": {
            "llm_template_versions": {
                "columns": ["id", "content", "is_active"],
                "rows": [
                    [1, "clean prompt", True],
                    [2, "x <!-- batch-pub-test --> y", True],
                    [3, "z <!-- rollback source -->", False],
                ],
            }
        }
    }
    hits = sync.scan_noise_in_payload(payload)
    assert len(hits) == 2
    assert hits[0]["id"] == 2
    assert "<!-- batch-pub-test -->" in hits[0]["markers"]
    assert hits[1]["id"] == 3


def test_write_and_read_payload_roundtrip(tmp_path: Path) -> None:
    """gzip 快照读写往返保持 kind/format/tables。"""
    payload = {
        "format_version": sync.FORMAT_VERSION,
        "kind": "llm_center_registry",
        "alembic_version": "abc123",
        "tables": {
            "llm_flows": {
                "columns": ["id", "flow_key"],
                "rows": [[1, "writing"]],
                "row_count": 1,
            }
        },
        "sequences": {},
        "excluded_tables": list(sync.EXCLUDED_TABLES),
    }
    out = tmp_path / "snap.json.gz"
    sync.write_payload(payload, out)
    loaded = sync.read_payload(out)
    assert loaded["kind"] == "llm_center_registry"
    assert loaded["alembic_version"] == "abc123"
    assert loaded["tables"]["llm_flows"]["rows"] == [[1, "writing"]]


def test_read_payload_rejects_wrong_kind(tmp_path: Path) -> None:
    """非 registry 快照必须拒绝。"""
    bad = {"format_version": 1, "kind": "other", "tables": {}}
    path = tmp_path / "bad.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(bad, fh)
    with pytest.raises(sync.LlmCenterRegistrySyncError, match="kind"):
        sync.read_payload(path)


def test_coerce_import_value_restores_timestamptz_iso_string() -> None:
    """JSON 往返后的 ISO 字符串必须还原为 datetime，否则 asyncpg 拒收 timestamptz。"""
    raw = "2026-04-10T01:16:15.214708+00:00"
    restored = sync.coerce_import_value(raw, "timestamp with time zone")
    assert isinstance(restored, datetime)
    assert restored.tzinfo is not None
    assert restored.isoformat() == raw


def test_coerce_import_value_does_not_parse_iso_in_text_columns() -> None:
    """text/varchar 列即使内容像 ISO 时间戳也必须保持 str，避免误伤 Prompt 正文。"""
    raw = "2026-04-10T01:16:15.214708+00:00"
    assert sync.coerce_import_value(raw, "text") == raw
    assert sync.coerce_import_value(raw, "character varying") == raw


def test_json_roundtrip_then_coerce_matches_original_datetime() -> None:
    """write_payload 把 datetime 写成 ISO 后，import 侧 coerce 必须还原为 datetime。"""
    original = datetime(2026, 4, 10, 1, 16, 15, 214708, tzinfo=timezone.utc)
    payload = {
        "format_version": sync.FORMAT_VERSION,
        "kind": "llm_center_registry",
        "tables": {
            "llm_flows": {
                "columns": ["id", "created_at"],
                "rows": [[1, original]],
                "row_count": 1,
            }
        },
    }
    raw = json.dumps(payload, ensure_ascii=False, default=sync._json_default)
    loaded = json.loads(raw)
    cell = loaded["tables"]["llm_flows"]["rows"][0][1]
    assert isinstance(cell, str)
    restored = sync.coerce_import_value(cell, "timestamp with time zone")
    assert restored == original


def test_import_registry_binds_coerced_values_from_pg_column_types() -> None:
    """import 必须按 information_schema 列类型调用 coerce，不能把 ISO 字符串直接绑定。"""
    import inspect

    src = inspect.getsource(sync.import_registry)
    assert "coerce_import_value" in src
    assert "_table_column_types" in src


def _snapshot_tables(extra_columns: dict[str, list[str]] | None = None) -> dict:
    """构造含全部同步表的最小快照 tables。"""
    tables: dict = {}
    extras = extra_columns or {}
    for name in sync.TABLES_INSERT_ORDER:
        columns = ["id", *extras.get(name, [])]
        tables[name] = {"columns": columns, "rows": [], "row_count": 0}
    return tables


def test_ensure_snapshot_columns_exist_allows_unrelated_alembic_skew() -> None:
    """本地 alembic 超前但 llm_* 列齐全时必须放行（: b8d0f2a4c6e8 vs a7c9e1f3b5d7）。"""
    tables = _snapshot_tables()
    live = {name: ["id", "created_at"] for name in sync.TABLES_INSERT_ORDER}
    sync.ensure_snapshot_columns_exist(tables, live)


def test_ensure_snapshot_columns_exist_rejects_missing_live_column() -> None:
    """目标库缺少快照列时必须拒绝，这才是真实 schema 不兼容。"""
    tables = _snapshot_tables({"llm_nodes": ["new_col"]})
    live = {name: ["id"] for name in sync.TABLES_INSERT_ORDER}
    with pytest.raises(sync.LlmCenterRegistrySyncError, match="缺少列"):
        sync.ensure_snapshot_columns_exist(tables, live)


def test_import_registry_does_not_hard_fail_on_alembic_head_mismatch() -> None:
    """import 不得用全局 alembic head 精确相等拒绝；应走 llm_* 列校验。"""
    import inspect

    src = inspect.getsource(sync.import_registry)
    assert "Alembic 版本不一致" not in src
    assert "ensure_snapshot_columns_exist" in src
    assert "snap_rev != local_rev" in src


def test_module_docstring_states_single_responsibility() -> None:
    """脚本 docstring 必须声明职责/不做。"""
    doc = sync.__doc__ or ""
    assert "职责" in doc
    assert "不做" in doc
    assert "execution_traces" in doc or "traces" in doc.lower()
