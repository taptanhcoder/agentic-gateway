"""Source registry, durable jobs, immutable evidence and snapshot history."""

from alembic import op

revision = "0002_source_registry"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade():
    # Frozen PostgreSQL DDL: future runtime model changes cannot rewrite history.
    ddl = """
    CREATE TABLE sources (
        id text PRIMARY KEY, fixture_name text NOT NULL,
        enabled boolean NOT NULL DEFAULT true,
        generation bigint NOT NULL DEFAULT 0 CHECK (generation >= 0),
        current_snapshot_id text,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
    );
    CREATE TABLE jobs (
        id text PRIMARY KEY, source_id text NOT NULL REFERENCES sources(id),
        revision text NOT NULL, generation bigint NOT NULL,
        status text NOT NULL DEFAULT 'queued'
            CHECK (status IN ('queued','running','succeeded','failed','superseded')),
        fencing_token bigint NOT NULL DEFAULT 0,
        lease_until timestamptz, sync_run_id text,
        error_code text, pages_read integer NOT NULL DEFAULT 0,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        finished_at timestamptz,
        UNIQUE (source_id, generation)
    );
    CREATE INDEX jobs_claim_idx ON jobs(status, lease_until, created_at);
    CREATE TABLE sync_runs (
        id text PRIMARY KEY, job_id text NOT NULL REFERENCES jobs(id),
        fencing_token bigint NOT NULL,
        status text NOT NULL CHECK (status IN
            ('running','succeeded','failed','superseded','abandoned','interrupted')),
        pages_read integer NOT NULL DEFAULT 0, total_pages integer,
        error_code text, diff jsonb,
        started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        finished_at timestamptz,
        UNIQUE (job_id, fencing_token)
    );
    ALTER TABLE jobs ADD CONSTRAINT jobs_run_fk
        FOREIGN KEY (sync_run_id) REFERENCES sync_runs(id);
    CREATE TABLE staging_records (
        run_id text NOT NULL REFERENCES sync_runs(id),
        position text NOT NULL, record jsonb NOT NULL,
        PRIMARY KEY (run_id, position)
    );
    CREATE TABLE offerings (
        id text PRIMARY KEY, provider text NOT NULL, region text NOT NULL,
        base_url text NOT NULL, upstream_model text NOT NULL, task text NOT NULL,
        UNIQUE (provider, region, base_url, upstream_model, task)
    );
    CREATE TABLE observations (
        id text PRIMARY KEY, source_id text NOT NULL REFERENCES sources(id),
        revision text NOT NULL, position text NOT NULL, parser_version text NOT NULL,
        offering_id text NOT NULL REFERENCES offerings(id), content_hash text NOT NULL,
        record jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE (source_id, revision, position, parser_version)
    );
    CREATE TABLE snapshots (
        id text PRIMARY KEY, source_id text NOT NULL REFERENCES sources(id),
        run_id text NOT NULL UNIQUE REFERENCES sync_runs(id), revision text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE (source_id, id)
    );
    ALTER TABLE sources ADD CONSTRAINT sources_snapshot_fk
        FOREIGN KEY (id, current_snapshot_id) REFERENCES snapshots(source_id, id);
    CREATE TABLE snapshot_memberships (
        snapshot_id text NOT NULL REFERENCES snapshots(id),
        observation_id text NOT NULL REFERENCES observations(id),
        PRIMARY KEY (snapshot_id, observation_id)
    );
    CREATE INDEX observations_offering_idx ON observations(offering_id);
    """
    for statement in ddl.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade():
    op.execute("ALTER TABLE sources DROP CONSTRAINT sources_snapshot_fk")
    op.execute("ALTER TABLE jobs DROP CONSTRAINT jobs_run_fk")
    for table in ("snapshot_memberships", "snapshots", "observations", "offerings",
                  "staging_records", "sync_runs", "jobs", "sources"):
        op.drop_table(table)
