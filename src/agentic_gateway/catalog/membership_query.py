"""Current membership links, including unambiguous 3C official model evidence."""
from sqlalchemy import and_, func, literal, or_, select, union_all
from .schema import memberships, observations, offerings, snapshots, sources


def current_links():
    active = select(observations.c.id.label("observation_id"), observations.c.offering_id,
        observations.c.source_id, observations.c.fields, observations.c.provenance,
        sources.c.connector, sources.c.provider.label("source_provider"), sources.c.catalog_scope,
        memberships.c.snapshot_id, snapshots.c.run_id.label("published_run_id"))
    active = active.select_from(sources.join(memberships, sources.c.current_snapshot_id == memberships.c.snapshot_id)
        .join(observations, observations.c.id == memberships.c.observation_id)
        .join(snapshots, snapshots.c.id == memberships.c.snapshot_id)).cte("current_observations")
    direct = select(active.c.offering_id, active.c.observation_id, active.c.source_id,
        active.c.snapshot_id, active.c.published_run_id, literal("direct").label("association"))
    direct = direct.where(active.c.offering_id.is_not(None))
    discovery, official = active.alias("discovery"), active.alias("official")
    model = official.c.fields["upstream_model"]["value"].astext
    task = official.c.fields["task"]["value"].astext
    endpoint = official.c.fields["base_url"]["value"].astext
    endpoint_scope = official.c.fields["base_url"]["scope"].astext
    candidates = select(official.c.observation_id, offerings.c.id.label("offering_id")).select_from(
        official.join(discovery, and_(
            discovery.c.provenance["provider"].astext == official.c.provenance["provider"].astext,
            discovery.c.provenance["catalog_scope"].astext == official.c.provenance["catalog_scope"].astext))
        .join(offerings, offerings.c.id == discovery.c.offering_id))
    candidates = candidates.where(official.c.offering_id.is_(None),
        official.c.connector.in_(["nvidia_hosted","siliconflow_cn","siliconflow_cn_docs"]),
        official.c.source_provider == offerings.c.provider,
        official.c.catalog_scope == discovery.c.provenance["catalog_scope"].astext,
        official.c.provenance["provider"].astext == offerings.c.provider,
        model == offerings.c.upstream_model,
        or_(task.is_(None), task == offerings.c.task),
        or_(endpoint_scope.is_(None), endpoint_scope != "model", endpoint == offerings.c.base_url)).distinct().cte("official_candidates")
    unique = select(candidates.c.observation_id, func.min(candidates.c.offering_id).label("offering_id"))
    unique = unique.group_by(candidates.c.observation_id).having(func.count() == 1).cte("unique_official_match")
    resolved = select(unique.c.offering_id, active.c.observation_id, active.c.source_id,
        active.c.snapshot_id, active.c.published_run_id, literal("exact_official").label("association"))
    resolved = resolved.select_from(unique.join(active, unique.c.observation_id == active.c.observation_id))
    return union_all(direct, resolved).cte("current_offering_links")
