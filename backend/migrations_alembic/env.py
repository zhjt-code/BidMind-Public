"""Alembic environment configuration.

Handles:
- Async-to-sync DATABASE_URL conversion (asyncpg -> psycopg2)
- Model metadata auto-discovery via app.models import
- PGVector type registration for autogenerate
"""

import os
import re
from logging.config import fileConfig

from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool
from alembic import context

load_dotenv()

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _get_sync_url() -> str:
    """Convert async DATABASE_URL to sync format for Alembic.

    postgresql+asyncpg://... -> postgresql+psycopg2://...
    """
    raw = os.getenv("DATABASE_URL", "")
    if not raw:
        raise ValueError(
            "DATABASE_URL not set. Alembic requires a database connection.\n"
            "Set it in .env or as an environment variable."
        )
    return re.sub(r"postgresql\+asyncpg", "postgresql+psycopg2", raw)


config.set_main_option("sqlalchemy.url", _get_sync_url())

# ---------------------------------------------------------------------------
# Import all models so that Base.metadata reflects the full schema.
# The wildcard import from app.models.__init__ pulls every ORM model.
# ---------------------------------------------------------------------------
from app.core.database import Base  # noqa: E402
import app.models  # noqa: E402, F401  -- side-effect: registers all tables

target_metadata = Base.metadata


def include_object(object, name, type_, reflected, compare_to):
    """Filter objects during autogenerate.

    Skip the Alembic version table itself.
    """
    if type_ == "table" and name == "alembic_version":
        return False
    return True


def _is_jsonb_json_swap(inspector_column, metadata_column):
    """Return True when the only type diff is JSONB <-> JSON on PostgreSQL.

    PostgreSQL treats JSON and JSONB as distinct but our ORM uses sa.JSON()
    which maps to JSON while PG often stores as JSONB.  This is harmless.
    """
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy import JSON

    old_t = type(inspector_column.get("type", None))
    new_t = type(metadata_column.type)
    return {old_t, new_t} <= {JSONB, JSON}


def process_revision_directives(context, revision, directives):
    """Strip comment-only and JSONB<->JSON diffs from autogenerate output.

    These diffs are cosmetic and produce migration noise.
    """
    if not directives:
        return
    script = directives[0]
    if not script.upgrade_ops:
        return

    from alembic.operations.ops import ModifyTableOps, AlterColumnOp

    kept = []
    for op in script.upgrade_ops.ops:
        if isinstance(op, ModifyTableOps):
            filtered = []
            for sub in op.ops:
                if isinstance(sub, AlterColumnOp):
                    only_comment = (
                        sub.modify_comment is not False
                        and sub.modify_type is None
                        and sub.modify_nullable is None
                        and sub.modify_server_default is False
                    )
                    if only_comment:
                        continue
                filtered.append(sub)
            if filtered:
                op.ops = filtered
                kept.append(op)
        else:
            kept.append(op)
    script.upgrade_ops.ops = kept


def render_item(type_, obj, autogen_context):
    """Custom rendering for non-standard column types.

    Ensures pgvector's Vector type is properly rendered in migration scripts.
    """
    if type_ == "type" and hasattr(obj, "__class__"):
        cls_name = obj.__class__.__name__
        module = obj.__class__.__module__ or ""
        if cls_name.upper() == "VECTOR" and "pgvector" in module:
            autogen_context.imports.add("from pgvector.sqlalchemy import Vector")
            dim = getattr(obj, "dim", None)
            if dim is not None:
                return f"Vector({dim})"
            return "Vector()"
    return False


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without DB connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (connect to DB)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        def _compare_type(context, inspected_column, metadata_column,
                          inspected_type, metadata_type):
            """Suppress JSONB <-> JSON diffs on PostgreSQL.

            PG's JSON and JSONB are functionally equivalent for our ORM usage.
            Returning False tells Alembic the types are "the same".
            """
            from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
            from sqlalchemy.types import JSON as SA_JSON

            names = {type(inspected_type).__name__, type(metadata_type).__name__}
            if names <= {"JSONB", "JSON"}:
                return False
            return None  # let Alembic decide for all other types

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            render_item=render_item,
            compare_type=_compare_type,
            process_revision_directives=process_revision_directives,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
