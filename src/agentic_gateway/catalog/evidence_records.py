"""Validate evidence records without forcing incomplete facts into offerings."""
from copy import deepcopy
from .identity import normalize


def normalize_record(record):
    if not record.get("evidence_record"):
        return normalize(record)
    result = deepcopy(record)
    if result.get("offering") is not None:
        result["offering"] = normalize(result["offering"])
    provenance = result["provenance"]
    for key in ("locator", "revision", "parser_version", "position", "provider", "catalog_scope", "mode"):
        if not provenance.get(key):
            raise ValueError("missing_evidence_provenance")
    if provenance["mode"] not in {"fixture", "live"}:
        raise ValueError("invalid_evidence_mode")
    if not isinstance(result["claims"], dict):
        raise ValueError("invalid_field_claims")
    return result


def stable_record(record):
    result = deepcopy(record)
    if result.get("evidence_record"):
        result["provenance"].pop("read_at", None)
    return result
