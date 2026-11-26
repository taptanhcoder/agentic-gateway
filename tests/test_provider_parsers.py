from pathlib import Path
import httpx
import pytest

from agentic_gateway.providers.catalog import transport
from agentic_gateway.providers.catalog.connectors import parse_nvidia, parse_sf_api, parse_sf_docs
from agentic_gateway.providers.catalog.transport import CatalogError, Document, NVIDIA, SF_API, SF_DOCS

FIXTURES = Path(__file__).parent / "fixtures"


def doc(name, locator):
    return Document(locator, (FIXTURES / name).read_text(), mode="fixture", fetched_at="2026-11-26T00:00:00+00:00")


def test_nvidia_model_id_and_provider_url_are_distinct_claims():
    row, = parse_nvidia(doc("nvidia-hosted.html", NVIDIA))
    assert row["offering"] is None
    assert row["claims"]["upstream_model"]["value"] == "meta/llama-3.3-70b-instruct"
    assert row["claims"]["base_url"]["scope"] == "provider"
    assert row["claims"]["task"]["scope"] == "model"
    assert "pricing" not in row["claims"] and "tool_calling" not in row["claims"]


def test_display_name_only_is_not_model_identity():
    original = doc("nvidia-hosted.html", NVIDIA)
    changed = Document(NVIDIA, original.body.replace("meta / llama-3.3-70b-instruct", "Llama Display Name"))
    row, = parse_nvidia(changed)
    assert "upstream_model" not in row["claims"]


@pytest.mark.parametrize("body", ["<h1>Sign in</h1>", "<h1>Error</h1>", "<h1>LLM APIs</h1>"])
def test_html_without_catalog_contract_is_not_empty_success(body):
    with pytest.raises(CatalogError):
        parse_nvidia(Document(NVIDIA, body))


def test_siliconflow_valid_empty_and_exact_prefix():
    row, = parse_sf_api(doc("siliconflow-models.json", SF_API))
    assert row["claims"]["upstream_model"]["value"] == "deepseek-ai/DeepSeek-V4-Flash"
    assert parse_sf_api(Document(SF_API, '{"object":"list","data":[]}')) == []
    row, = parse_sf_docs(doc("siliconflow-cline.html", SF_DOCS))
    assert "upstream_model" not in row["claims"]
    assert row["claims"]["base_url"]["scope"] == "provider"


@pytest.mark.parametrize("body", ['<h1>Login</h1>', '{"data":[]}', '{"object":"list","data":[],"error":"denied"}',
    '{"object":"list","data":[],"has_more":true}', '{"code":500,"data":[]}'])
def test_siliconflow_login_error_and_pagination_are_not_empty_catalog(body):
    with pytest.raises(CatalogError):
        parse_sf_api(Document(SF_API, body))


def test_transport_never_follows_login_or_leaks_credential(monkeypatch):
    calls = []
    real_client = httpx.Client

    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location":"https://account.siliconflow.cn/login"})

    monkeypatch.setattr(transport.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(CatalogError, match="catalog_login_required"):
        transport.fetch_document(SF_API, credential="fixture-secret")
    assert len(calls) == 1
    assert calls[0].method == "GET" and calls[0].url.host == "api.siliconflow.cn"
    with pytest.raises(CatalogError, match="credential_host_not_allowed"):
        transport.fetch_document(SF_DOCS, credential="fixture-secret")
    assert len(calls) == 1
