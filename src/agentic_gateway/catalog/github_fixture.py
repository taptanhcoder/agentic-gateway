"""Explicitly synthetic provenance adapter for 3C tests; NOT a GitHub connector."""
import json
from importlib.resources import files
from datetime import datetime, timezone

from agentic_gateway.providers.catalog.connectors import evidence, field
from agentic_gateway.providers.catalog.transport import Document


def read_fixture(claim):
    if claim.revision != "r1":
        raise ValueError("unknown_revision")
    body = files("agentic_gateway.catalog").joinpath("fixtures", "github-provenance.json").read_text()
    data = json.loads(body)
    document = Document("fixture://github/example/metadata-catalog/catalog.json", body,
                        mode="fixture", fetched_at=datetime.now(timezone.utc).isoformat())
    records = []
    for index, row in enumerate(data["records"]):
        offering = row["offering"]
        pointer = f"/records/{index}"
        claims = {key:field(value, "model", pointer + "/offering/" + key, str(value)) for key,value in offering.items()}
        if "pricing" in row:
            claims["pricing"] = field(row["pricing"], "model", pointer + "/pricing", str(row["pricing"]))
        records.append(evidence(document, "github_fixture", offering["provider"], row["catalog_scope"],
            pointer, claims, offering=offering, github=data["github"], synthetic=True))
    return records
