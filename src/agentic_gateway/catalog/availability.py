"""Local-only connector/configuration checks. Never performs a network read."""
import os
from .fixture_reader import load_fixture
from agentic_gateway.providers.catalog.transport import NVIDIA, SF_WEB, SF_DOCS, SF_API, SF_API_DOCS


def default_revision(source):
    return "r1" if source["connector"] in {"fixture", "github_fixture"} else "latest"


def connector_status(source, revision=None):
    kind = source["connector"]
    revision = revision or source.get("sync_revision") or default_revision(source)
    reason = None
    state = "available"
    if kind == "fixture":
        try:
            load_fixture(source["fixture_name"], revision)
        except (ValueError, TypeError):
            state, reason = "misconfigured", "fixture_configuration_invalid"
    elif kind == "github_fixture":
        if revision != "r1":
            state, reason = "misconfigured", "fixture_revision_unknown"
    elif kind in {"nvidia_hosted", "siliconflow_cn", "siliconflow_cn_docs"}:
        expected = {"nvidia_hosted":NVIDIA, "siliconflow_cn":SF_WEB, "siliconflow_cn_docs":SF_DOCS}[kind]
        if source["locator"] != expected:
            state, reason = "misconfigured", "connector_locator_mismatch"
        elif kind == "siliconflow_cn":
            config = source["config"] or {}
            if (config.get("catalog_api_locator") != SF_API or config.get("catalog_api_evidence_locator") != SF_API_DOCS
                or config.get("credential_env", "SILICONFLOW_CN_CATALOG_API_KEY") != "SILICONFLOW_CN_CATALOG_API_KEY"):
                state, reason = "misconfigured", "catalog_api_not_documented"
            elif not os.getenv("SILICONFLOW_CN_CATALOG_API_KEY"):
                state, reason = "blocked", "catalog_credential_missing"
    else:
        state, reason = "missing", "connector_not_supported"
    return {"name":kind, "status":state, "usable_for_schedule":state == "available", "reason_code":reason}
