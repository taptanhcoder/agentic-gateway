"""Configured metadata sources and field-level evidence, without inference."""
from alembic import op

revision = "0003_provider_evidence"
down_revision = "0002_source_registry"
branch_labels = None
depends_on = None


def upgrade():
    for statement in """
        ALTER TABLE sources ALTER COLUMN fixture_name DROP NOT NULL;
        ALTER TABLE sources ADD COLUMN connector text NOT NULL DEFAULT 'fixture';
        ALTER TABLE sources ADD COLUMN locator text;
        ALTER TABLE sources ADD COLUMN provider text;
        ALTER TABLE sources ADD COLUMN catalog_scope text;
        ALTER TABLE sources ADD COLUMN config jsonb NOT NULL DEFAULT '{}';
        ALTER TABLE observations ALTER COLUMN offering_id DROP NOT NULL;
        ALTER TABLE observations ADD COLUMN fields jsonb NOT NULL DEFAULT '{}';
        ALTER TABLE observations ADD COLUMN provenance jsonb NOT NULL DEFAULT '{}';
        ALTER TABLE sync_runs ADD COLUMN fetch_metadata jsonb NOT NULL DEFAULT '[]';
        CREATE TABLE catalog_documents (
            run_id text NOT NULL REFERENCES sync_runs(id), locator text NOT NULL,
            content_hash text NOT NULL, body text NOT NULL, read_at timestamptz NOT NULL,
            mode text NOT NULL CHECK (mode IN ('fixture','live')),
            PRIMARY KEY (run_id, locator)
        );
        ALTER TABLE jobs DROP CONSTRAINT jobs_status_check;
        ALTER TABLE jobs ADD CONSTRAINT jobs_status_check CHECK
            (status IN ('queued','running','succeeded','failed','blocked','superseded'));
        ALTER TABLE sync_runs DROP CONSTRAINT sync_runs_status_check;
        ALTER TABLE sync_runs ADD CONSTRAINT sync_runs_status_check CHECK
            (status IN ('running','succeeded','failed','blocked','superseded','abandoned','interrupted'))
    """.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade():
    # Downgrade is supported only when the added official sources have no data.
    # Never silently discard evidence to make an old schema fit.
    op.drop_table("catalog_documents")
    op.execute("ALTER TABLE observations ALTER COLUMN offering_id SET NOT NULL")
    op.execute("ALTER TABLE sources ALTER COLUMN fixture_name SET NOT NULL")
    op.execute("ALTER TABLE sync_runs DROP COLUMN fetch_metadata")
    for column in ("fields", "provenance"):
        op.execute(f"ALTER TABLE observations DROP COLUMN {column}")
    for column in ("connector", "locator", "provider", "catalog_scope", "config"):
        op.execute(f"ALTER TABLE sources DROP COLUMN {column}")
    op.execute("ALTER TABLE jobs DROP CONSTRAINT jobs_status_check")
    op.execute("ALTER TABLE jobs ADD CONSTRAINT jobs_status_check CHECK (status IN ('queued','running','succeeded','failed','superseded'))")
    op.execute("ALTER TABLE sync_runs DROP CONSTRAINT sync_runs_status_check")
    op.execute("ALTER TABLE sync_runs ADD CONSTRAINT sync_runs_status_check CHECK (status IN ('running','succeeded','failed','superseded','abandoned','interrupted'))")
