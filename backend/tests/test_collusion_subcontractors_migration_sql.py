""": subcontractors migration must stay on safe JSON ops."""

from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations_alembic"
    / "versions"
    / "2026_04_25_b2f7d8e9a0c1_split_subcontractors_from_metadata_info.py"
)


def test_metadata_info_migration_uses_json_ops_without_jsonb_cast() -> None:
    sql = MIGRATION_PATH.read_text(encoding="utf-8")

    assert "ADD COLUMN IF NOT EXISTS subcontractors_info JSON" in sql
    assert "replace(metadata_info::text, '\\\\u0000', '')::json" in sql
    assert "metadata_info_clean -> 'subcontractors'" in sql
    assert "json_typeof(normalized.metadata_info_clean)" in sql
    assert "json_each(normalized.metadata_info_clean)" in sql
    assert "json_each(COALESCE(ce.metadata_info, '{}'::json))" in sql
    assert "json_object_agg(item.key, item.value)" in sql
    assert "json_object_agg(merged.key, merged.value)" in sql
    assert "UNION ALL" in sql
    assert "SELECT 'subcontractors', ce.subcontractors_info" in sql

    assert "metadata_info::jsonb" not in sql
    assert "jsonb_typeof" not in sql
    assert "metadata_info::jsonb ? 'subcontractors'" not in sql
    assert "jsonb_build_object" not in sql
    assert "|| json_build_object" not in sql
    assert "json_typeof(metadata_info)" not in sql
    assert "metadata_info -> 'subcontractors' IS NOT NULL" not in sql
