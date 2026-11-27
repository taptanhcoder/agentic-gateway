import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

metadata = sa.MetaData()
S, I, T = sa.Text, sa.BigInteger, sa.DateTime(timezone=True)


def table(name, fields):
    return sa.Table(name, metadata, *(sa.Column(key, kind) for key, kind in fields))


policy = table("qualification_policy", [("id", S), ("profile_revision", S), ("suite_revision", S), ("spec", JSONB)])
runs = table("qualification_runs", [("id", S), ("job_id", S), ("deployment_id", S), ("generation", I),
    ("account_id", S), ("adapter_version", S), ("qualification_epoch", I), ("profile_revision", S),
    ("suite_revision", S), ("spec", JSONB), ("mode", S), ("fixture_scenario", S), ("max_probes", sa.Integer),
    ("status", S), ("reason_codes", JSONB), ("requested_by", S), ("created_at", T), ("started_at", T),
    ("deadline_at", T), ("finished_at", T)])
observations = table("qualification_observations", [("run_id", S), ("case_id", S), ("attempt_id", S),
    ("fencing_token", I), ("result", S), ("reason_code", S), ("assertions", JSONB),
    ("latency_ms", sa.Float), ("usage", JSONB), ("created_at", T), ("finished_at", T)])
verdicts = table("qualification_verdicts", [("id", S), ("run_id", S), ("decision", S),
    ("reason_codes", JSONB), ("created_at", T), ("expires_at", T)])
pins = table("published_models", [("id", S), ("route_version", I), ("snapshot_id", S)])
snapshots = table("route_snapshots", [("id", S), ("model_id", S), ("route_version", I), ("verdict_id", S),
    ("deployment_id", S), ("generation", I), ("published_by", S), ("published_at", T), ("rollback_of", S)])
