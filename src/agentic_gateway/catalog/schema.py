"""Core table mappings. Constraints are frozen in Alembic revisions."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

metadata = sa.MetaData()


def table(name, fields):
    return sa.Table(name, metadata, *(sa.Column(key, kind) for key, kind in fields))


S, I, T = sa.Text, sa.BigInteger, sa.DateTime(timezone=True)
sources = table("sources", [("id", S), ("fixture_name", S), ("enabled", sa.Boolean),
    ("generation", I), ("current_snapshot_id", S), ("created_at", T),
    ("connector", S), ("locator", S), ("provider", S), ("catalog_scope", S), ("config", JSONB),
    ("sync_interval_seconds", sa.Integer), ("next_sync_at", T), ("sync_revision", S)])
jobs = table("jobs", [("id", S), ("source_id", S), ("revision", S), ("generation", I),
    ("status", S), ("fencing_token", I), ("lease_until", T), ("sync_run_id", S),
    ("error_code", S), ("pages_read", sa.Integer), ("created_at", T), ("finished_at", T),
    ("trigger_kind", S), ("scheduled_for", T), ("kind", S), ("qualification_run_id", S)])
runs = table("sync_runs", [("id", S), ("job_id", S), ("fencing_token", I), ("status", S),
    ("pages_read", sa.Integer), ("total_pages", sa.Integer), ("error_code", S),
    ("diff", JSONB), ("started_at", T), ("finished_at", T), ("fetch_metadata", JSONB)])
staging = table("staging_records", [("run_id", S), ("position", S), ("record", JSONB)])
offerings = table("offerings", [("id", S), ("provider", S), ("region", S),
    ("base_url", S), ("upstream_model", S), ("task", S)])
observations = table("observations", [("id", S), ("source_id", S), ("revision", S),
    ("position", S), ("parser_version", S), ("offering_id", S), ("content_hash", S),
    ("record", JSONB), ("created_at", T), ("fields", JSONB), ("provenance", JSONB)])
snapshots = table("snapshots", [("id", S), ("source_id", S), ("run_id", S),
    ("revision", S), ("created_at", T)])
memberships = table("snapshot_memberships", [("snapshot_id", S), ("observation_id", S)])
documents = table("catalog_documents", [("run_id", S), ("locator", S), ("content_hash", S),
    ("body", S), ("read_at", T), ("mode", S)])
