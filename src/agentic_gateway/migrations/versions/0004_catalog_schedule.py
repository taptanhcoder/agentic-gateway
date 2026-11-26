"""Opt-in source schedules and durable per-period job identity."""
from alembic import op

revision = "0004_catalog_schedule"
down_revision = "0003_provider_evidence"
branch_labels = None
depends_on = None


def upgrade():
    for sql in """
        ALTER TABLE sources ADD COLUMN sync_interval_seconds integer;
        ALTER TABLE sources ADD COLUMN next_sync_at timestamptz;
        ALTER TABLE sources ADD COLUMN sync_revision text;
        ALTER TABLE sources ADD CONSTRAINT sources_schedule_check CHECK (
            (sync_interval_seconds IS NULL AND next_sync_at IS NULL) OR
            (sync_interval_seconds IS NOT NULL AND sync_interval_seconds BETWEEN 1 AND 31536000 AND next_sync_at IS NOT NULL));
        ALTER TABLE jobs ADD COLUMN trigger_kind text NOT NULL DEFAULT 'manual';
        ALTER TABLE jobs ADD COLUMN scheduled_for timestamptz;
        ALTER TABLE jobs ADD CONSTRAINT jobs_trigger_check CHECK (
            (trigger_kind = 'manual' AND scheduled_for IS NULL) OR
            (trigger_kind = 'scheduled' AND scheduled_for IS NOT NULL));
        ALTER TABLE jobs ADD CONSTRAINT jobs_scheduled_period_uq UNIQUE (source_id, scheduled_for);
        CREATE INDEX sources_due_idx ON sources(next_sync_at) WHERE enabled AND next_sync_at IS NOT NULL
    """.split(";"):
        if sql.strip():
            op.execute(sql)


def downgrade():
    op.execute("DROP INDEX sources_due_idx")
    op.execute("ALTER TABLE jobs DROP CONSTRAINT jobs_scheduled_period_uq")
    op.execute("ALTER TABLE jobs DROP CONSTRAINT jobs_trigger_check")
    op.execute("ALTER TABLE jobs DROP COLUMN scheduled_for")
    op.execute("ALTER TABLE jobs DROP COLUMN trigger_kind")
    op.execute("ALTER TABLE sources DROP CONSTRAINT sources_schedule_check")
    for name in ("sync_revision", "next_sync_at", "sync_interval_seconds"):
        op.execute(f"ALTER TABLE sources DROP COLUMN {name}")
