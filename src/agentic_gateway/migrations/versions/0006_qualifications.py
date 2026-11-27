"""Qualification evidence and compare-and-swap pinned route history."""
from alembic import op

revision = "0006_qualifications"
down_revision = "0005_deployments"
branch_labels = None
depends_on = None


def upgrade():
    for sql in """
        ALTER TABLE upstream_accounts ADD COLUMN enabled boolean NOT NULL DEFAULT true;
        ALTER TABLE deployments ADD COLUMN qualification_epoch bigint NOT NULL DEFAULT 0;
        ALTER TABLE jobs ALTER COLUMN source_id DROP NOT NULL;
        ALTER TABLE jobs ADD COLUMN kind text NOT NULL DEFAULT 'catalog' CHECK (kind IN ('catalog','qualification'));
        ALTER TABLE jobs ADD COLUMN qualification_run_id text;
        CREATE TABLE qualification_policy (
            id text PRIMARY KEY, profile_revision text NOT NULL, suite_revision text NOT NULL,
            spec jsonb NOT NULL);
        INSERT INTO qualification_policy VALUES ('text', 'text-v1', 'text-smoke-v1',
            '{"deadline_seconds": 12,"probe_deadline_seconds": 5,"max_probes": 2,"max_output_tokens": 16,"ttl_seconds": 3600,"required":["envelope_valid","text_nonempty","within_deadline","usage_recorded"],"cases":[{"id":"short-text","text":"Write one short greeting."},{"id":"instructed-text","text":"Describe a clear sky briefly.","instructions":"Respond with a short plain text sentence."}]}');
        CREATE TABLE qualification_runs (
            id text PRIMARY KEY, job_id text NOT NULL UNIQUE REFERENCES jobs(id),
            deployment_id text NOT NULL, generation bigint NOT NULL,
            account_id text NOT NULL REFERENCES upstream_accounts(id),
            adapter_version text NOT NULL, qualification_epoch bigint NOT NULL,
            profile_revision text NOT NULL, suite_revision text NOT NULL, spec jsonb NOT NULL,
            mode text NOT NULL CHECK (mode IN ('fixture','live')), fixture_scenario text,
            max_probes integer NOT NULL CHECK (max_probes BETWEEN 1 AND 2),
            status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','completed','blocked')),
            reason_codes jsonb NOT NULL DEFAULT '[]', requested_by text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            started_at timestamptz, deadline_at timestamptz, finished_at timestamptz,
            FOREIGN KEY (deployment_id,generation) REFERENCES deployment_versions(deployment_id,generation));
        ALTER TABLE jobs ADD CONSTRAINT jobs_qualification_run_fk
            FOREIGN KEY (qualification_run_id) REFERENCES qualification_runs(id) DEFERRABLE INITIALLY DEFERRED;
        ALTER TABLE jobs ADD CONSTRAINT jobs_payload_check CHECK (
            (kind='catalog' AND source_id IS NOT NULL AND qualification_run_id IS NULL) OR
            (kind='qualification' AND source_id IS NULL AND qualification_run_id IS NOT NULL AND sync_run_id IS NULL));
        CREATE TABLE qualification_observations (
            run_id text NOT NULL REFERENCES qualification_runs(id), case_id text NOT NULL,
            attempt_id text NOT NULL UNIQUE REFERENCES inference_attempts(id), fencing_token bigint NOT NULL,
            result text NOT NULL DEFAULT 'unknown', reason_code text NOT NULL DEFAULT 'interrupted_unknown',
            assertions jsonb NOT NULL DEFAULT '{}', latency_ms double precision, usage jsonb NOT NULL DEFAULT '{"status":"unknown"}',
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(), finished_at timestamptz,
            PRIMARY KEY (run_id,case_id));
        CREATE TABLE qualification_verdicts (
            id text PRIMARY KEY, run_id text NOT NULL UNIQUE REFERENCES qualification_runs(id),
            decision text NOT NULL CHECK (decision IN ('eligible','ineligible','inconclusive')),
            reason_codes jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            expires_at timestamptz NOT NULL);
        CREATE TABLE published_models (
            id text PRIMARY KEY, route_version bigint NOT NULL CHECK (route_version>0), snapshot_id text NOT NULL);
        CREATE TABLE route_snapshots (
            id text PRIMARY KEY, model_id text NOT NULL REFERENCES published_models(id) DEFERRABLE INITIALLY DEFERRED,
            route_version bigint NOT NULL, verdict_id text NOT NULL REFERENCES qualification_verdicts(id),
            deployment_id text NOT NULL, generation bigint NOT NULL,
            published_by text NOT NULL, published_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            rollback_of text REFERENCES route_snapshots(id), UNIQUE(model_id,route_version), UNIQUE(model_id,id),
            FOREIGN KEY(deployment_id,generation) REFERENCES deployment_versions(deployment_id,generation));
        ALTER TABLE published_models ADD CONSTRAINT published_models_snapshot_fk
            FOREIGN KEY(id,snapshot_id) REFERENCES route_snapshots(model_id,id) DEFERRABLE INITIALLY DEFERRED
    """.split(";"):
        if sql.strip():
            op.execute(sql)


def downgrade():
    op.execute("ALTER TABLE published_models DROP CONSTRAINT published_models_snapshot_fk")
    for name in ("route_snapshots", "published_models", "qualification_verdicts", "qualification_observations"):
        op.drop_table(name)
    op.execute("ALTER TABLE jobs DROP CONSTRAINT jobs_payload_check")
    op.execute("ALTER TABLE jobs DROP CONSTRAINT jobs_qualification_run_fk")
    op.drop_table("qualification_runs")
    op.drop_table("qualification_policy")
    op.drop_column("jobs", "qualification_run_id")
    op.drop_column("jobs", "kind")
    op.execute("ALTER TABLE jobs ALTER COLUMN source_id SET NOT NULL")
    op.drop_column("deployments", "qualification_epoch")
    op.drop_column("upstream_accounts", "enabled")
