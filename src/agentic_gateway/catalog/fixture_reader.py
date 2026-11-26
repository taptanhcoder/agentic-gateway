"""Load only named, packaged fixtures. Never interpret a source as a path/URL."""

import json
from importlib.resources import files

NAMES = frozenset({"catalog-a", "catalog-b"})


def load_fixture(name: str, revision: str) -> dict:
    if name not in NAMES:
        raise ValueError("unknown_fixture")
    data = json.loads(files("agentic_gateway.catalog").joinpath("fixtures", name + ".json").read_text())
    if revision not in data:
        raise ValueError("unknown_revision")
    return data[revision]
