# DEPRECATED - Legacy Migration Scripts

This directory contains hand-written SQL migration scripts that were used
before Alembic was introduced. They are kept for historical reference only.

**DO NOT execute these scripts directly.**

All schema changes must now go through Alembic:

```bash
cd backend

# Apply all pending migrations
alembic upgrade head

# Create a new migration
alembic revision --autogenerate -m "description of change"

# Rollback the last migration
alembic downgrade -1
```

See `backend/migrations_alembic/` for the active migration environment.
