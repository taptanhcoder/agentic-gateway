"""Pinned routes: immutable history, current-policy validation and version CAS."""
import re
from uuid import uuid4

from sqlalchemy import func, insert, select, text, update

from agentic_gateway.providers.inference.adapter import ADAPTER_VERSION
from agentic_gateway.providers.inference.deployments import accounts, deployments, versions
from .schema import pins, policy, runs, snapshots, verdicts


class RouteError(Exception):
    pass


def approved(connection, verdict_id):
    verdict = connection.execute(select(verdicts).where(verdicts.c.id == verdict_id)).mappings().one_or_none()
    if verdict is None:
        raise LookupError("verdict_not_found")
    run = connection.execute(select(runs).where(runs.c.id == verdict["run_id"])).mappings().one()
    current_policy = connection.execute(select(policy).where(policy.c.id == "text").with_for_update(read=True)).mappings().one()
    deployment = connection.execute(select(deployments).where(deployments.c.id == run["deployment_id"])
        .with_for_update(read=True)).mappings().one()
    account = connection.execute(select(accounts).where(accounts.c.id == run["account_id"])
        .with_for_update(read=True)).mappings().one()
    version = connection.execute(select(versions).where(versions.c.deployment_id == run["deployment_id"],
        versions.c.generation == deployment["generation"])).mappings().one()
    if run["mode"] != "live":
        raise RouteError("live_verdict_required")
    if run["status"] != "completed" or verdict["decision"] != "eligible":
        raise RouteError("eligible_verdict_required")
    if verdict["expires_at"] <= connection.scalar(select(func.clock_timestamp())):
        raise RouteError("verdict_expired")
    if not deployment["enabled"] or not account["enabled"] or deployment["qualification_epoch"] != run["qualification_epoch"]:
        raise RouteError("deployment_or_account_revoked")
    if deployment["generation"] != run["generation"] or version["account_id"] != run["account_id"]:
        raise RouteError("deployment_generation_changed")
    if run["adapter_version"] != version["adapter_version"] or run["adapter_version"] != ADAPTER_VERSION:
        raise RouteError("adapter_revision_changed")
    if (run["profile_revision"] != current_policy["profile_revision"] or
        run["suite_revision"] != current_policy["suite_revision"] or run["spec"] != current_policy["spec"]):
        raise RouteError("qualification_policy_changed")
    return dict(run), dict(verdict)


def publish(engine, model_id, verdict_id, expected_version, actor, *, rollback_of=None):
    if not re.fullmatch(r"ag/[A-Za-z0-9][A-Za-z0-9._/-]{0,99}", model_id) or type(expected_version) is not int or expected_version < 0:
        raise RouteError("invalid_pin_input")
    with engine.begin() as connection:
        # Covers the absent-row race at first publish as well as later CAS updates.
        connection.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": "pin:" + model_id})
        pin = connection.execute(select(pins).where(pins.c.id == model_id).with_for_update()).mappings().one_or_none()
        if (pin["route_version"] if pin else 0) != expected_version:
            raise RouteError("route_version_conflict")
        if rollback_of:
            previous = connection.execute(select(snapshots).where(snapshots.c.id == rollback_of,
                snapshots.c.model_id == model_id)).mappings().one_or_none()
            if previous is None:
                raise LookupError("route_snapshot_not_found")
            verdict_id = previous["verdict_id"]
        run, verdict = approved(connection, verdict_id)
        snapshot_id, version = str(uuid4()), expected_version + 1
        values = dict(id=snapshot_id, model_id=model_id, route_version=version, verdict_id=verdict["id"],
            deployment_id=run["deployment_id"], generation=run["generation"], published_by=actor, rollback_of=rollback_of)
        connection.execute(insert(snapshots).values(**values))
        if pin:
            changed = connection.execute(update(pins).where(pins.c.id == model_id, pins.c.route_version == expected_version)
                .values(route_version=version, snapshot_id=snapshot_id)).rowcount
            if changed != 1:
                raise RouteError("route_version_conflict")
        else:
            connection.execute(insert(pins).values(id=model_id, route_version=version, snapshot_id=snapshot_id))
        # Expiration is checked again after the database writes, immediately before commit.
        if verdict["expires_at"] <= connection.scalar(select(func.clock_timestamp())):
            raise RouteError("verdict_expired")
        return {**values, "expires_at": verdict["expires_at"], "run_id": run["id"], "usable": True}


def read_route(engine, model_id):
    with engine.begin() as connection:
        pin = connection.execute(select(pins).where(pins.c.id == model_id).with_for_update(read=True)).mappings().one_or_none()
        if pin is None:
            raise LookupError("pinned_model_not_found")
        row = connection.execute(select(snapshots).where(snapshots.c.id == pin["snapshot_id"])).mappings().one()
        run, verdict = approved(connection, row["verdict_id"])
        return {**dict(row), "usable": True, "run_id": run["id"], "expires_at": verdict["expires_at"]}


def snapshot_detail(engine, snapshot_id):
    with engine.begin() as connection:
        row = connection.execute(select(snapshots).where(snapshots.c.id == snapshot_id)).mappings().one_or_none()
        if row is None:
            raise LookupError("route_snapshot_not_found")
        result = dict(row)
        try:
            run, verdict = approved(connection, row["verdict_id"])
            result.update(usable=True, invalid_reason=None, run_id=run["id"], expires_at=verdict["expires_at"])
        except RouteError as error:
            result.update(usable=False, invalid_reason=str(error))
        return result
