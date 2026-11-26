"""Immutable deployment generations and sanitized single-invocation records."""
from alembic import op

revision = "0005_deployments"
down_revision = "0004_catalog_schedule"
branch_labels = None
depends_on = None


def upgrade():
    for sql in """
        CREATE TABLE upstream_accounts (
            id text PRIMARY KEY, provider text NOT NULL, label text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp());
        CREATE TABLE secret_refs (
            id text PRIMARY KEY, env_name text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp());
        CREATE TABLE deployments (
            id text PRIMARY KEY, generation bigint NOT NULL CHECK (generation > 0),
            enabled boolean NOT NULL DEFAULT true);
        CREATE TABLE deployment_versions (
            deployment_id text NOT NULL REFERENCES deployments(id), generation bigint NOT NULL,
            offering_id text NOT NULL REFERENCES offerings(id),
            account_id text NOT NULL REFERENCES upstream_accounts(id),
            secret_ref_id text NOT NULL REFERENCES secret_refs(id),
            provider text NOT NULL, endpoint text NOT NULL, upstream_model text NOT NULL,
            region text NOT NULL, adapter_version text NOT NULL,
            evidence jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (deployment_id, generation));
        ALTER TABLE deployments ADD CONSTRAINT deployments_current_version_fk
            FOREIGN KEY (id,generation) REFERENCES deployment_versions(deployment_id,generation)
            DEFERRABLE INITIALLY DEFERRED;
        CREATE TABLE inference_attempts (
            id text PRIMARY KEY, deployment_id text NOT NULL, generation bigint NOT NULL,
            mode text NOT NULL CHECK (mode IN ('fixture','live')),
            started_at timestamptz NOT NULL DEFAULT clock_timestamp(), finished_at timestamptz,
            result_code text NOT NULL DEFAULT 'interrupted_unknown',
            provider_outcome text NOT NULL DEFAULT 'unknown',
            report jsonb NOT NULL,
            FOREIGN KEY (deployment_id,generation) REFERENCES deployment_versions(deployment_id,generation))
    """.split(";"):
        if sql.strip():
            op.execute(sql)


def downgrade():
    op.drop_table("inference_attempts")
    op.execute("ALTER TABLE deployments DROP CONSTRAINT deployments_current_version_fk")
    for name in ("deployment_versions","deployments","secret_refs","upstream_accounts"):
        op.drop_table(name)
