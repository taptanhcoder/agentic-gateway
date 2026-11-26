"""Offering identity is independent of its source/evidence."""

import hashlib
import json
from urllib.parse import urlsplit, urlunsplit

PARSER_VERSION = "fixture-v1"


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def normalize(record: dict) -> dict:
    fields = ("provider", "region", "base_url", "upstream_model", "task")
    if set(record) != set(fields) or not all(isinstance(record[k], str) and record[k].strip() for k in fields):
        raise ValueError("invalid_fixture_record")
    result = {key: record[key].strip() for key in fields}
    result["provider"] = result["provider"].lower()
    result["region"] = result["region"].lower()
    if result["task"] not in {"chat", "embedding", "rerank"}:
        raise ValueError("invalid_task")
    url = urlsplit(result["base_url"])
    if (url.scheme.lower() != "https" or not url.hostname or url.username is not None
        or url.password is not None or url.query or url.fragment
        or any(part in {".", ".."} for part in url.path.split("/"))):
        raise ValueError("invalid_base_url")
    hostname = url.hostname.encode("idna").decode().lower()
    if ":" in hostname:
        hostname = f"[{hostname}]"
    authority = hostname if url.port in {None, 443} else f"{hostname}:{url.port}"
    result["base_url"] = urlunsplit(("https", authority, url.path.rstrip("/"), "", ""))
    return result
