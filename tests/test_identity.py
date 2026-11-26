import pytest

from agentic_gateway.catalog.identity import digest, normalize


def record(**changes):
    return {"provider":"demo-provider", "region":"global", "base_url":"https://catalog-a.example/v1",
            "upstream_model":"alpha", "task":"chat", **changes}


def test_identity_normalizes_only_documented_fields():
    canonical = normalize(record())
    assert normalize(record(provider=" DEMO-provider ", region="GLOBAL", base_url="https://CATALOG-A.example:443/v1/")) == canonical
    assert digest(normalize(record(task="embedding"))) != digest(canonical)
    assert digest(normalize(record(upstream_model="Alpha"))) != digest(canonical)
    assert digest(normalize(record(base_url="https://catalog-a.example/V1"))) != digest(canonical)


@pytest.mark.parametrize("url", ["http://catalog-a.example/v1", "https://secret@catalog-a.example/v1",
    "https://catalog-a.example/v1?key=secret", "https://catalog-a.example/v1#fragment",
    "https://catalog-a.example/../v1"])
def test_credential_or_ambiguous_urls_are_rejected(url):
    with pytest.raises(ValueError):
        normalize(record(base_url=url))
