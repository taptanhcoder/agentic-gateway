"""Conservative parsers: model facts and provider facts remain separate."""
import json
import os
import re

from bs4 import BeautifulSoup

from .transport import CatalogError, NVIDIA, SF_API, SF_API_DOCS, SF_DOCS, SF_WEB, fetch_document

PARSER_VERSIONS = {"nvidia_hosted": "nvidia-llm-html-v1", "siliconflow_cn": "siliconflow-models-v1",
                   "siliconflow_cn_docs": "siliconflow-cline-v1", "github_fixture": "github-provenance-fixture-v1"}


def field(value, scope, pointer, excerpt):
    return {"value": value, "scope": scope, "pointer": pointer, "excerpt": excerpt[:1000]}


def evidence(document, connector, provider, scope, position, claims, offering=None, **extra):
    return {"evidence_record": True, "offering": offering, "claims": claims,
        "provenance": {"locator": document.locator, "revision": ("fixture:" if document.mode == "fixture" else "") + document.sha256,
            "content_hash": document.sha256, "read_at": document.fetched_at,
            "parser_version": PARSER_VERSIONS[connector], "position": position,
            "provider": provider, "catalog_scope": scope, "mode": document.mode, **extra}}


def parse_nvidia(document):
    soup = BeautifulSoup(document.body, "html.parser")
    heading = next((node for node in soup.find_all("h1") if node.get_text(" ", strip=True).strip("# ") == "LLM APIs"), None)
    text = soup.get_text(" ", strip=True)
    if not heading or not any(a.get("href", "").rstrip("/") == "https://integrate.api.nvidia.com" for a in soup.find_all("a")) or "/v1/chat/completions" not in text:
        raise CatalogError("nvidia_catalog_structure_changed")
    tables = [table for table in soup.find_all("table") if
        [cell.get_text(" ", strip=True).lower() for cell in table.select("thead th")] == ["model", "endpoint"]]
    if not tables:
        raise CatalogError("nvidia_catalog_missing_tables")
    records = []
    for table_index, table in enumerate(tables, 1):
        for row_index, row in enumerate(table.select("tbody tr"), 1):
            cells = row.find_all("td", recursive=False)
            if len(cells) != 2 or not cells[0].get_text(strip=True):
                raise CatalogError("nvidia_catalog_invalid_row")
            label, endpoint = (cell.get_text(" ", strip=True) for cell in cells)
            pointer = f"table[{table_index}]/tbody/tr[{row_index}]"
            match = re.fullmatch(r"([A-Za-z0-9_.-]+)\s*/\s*([A-Za-z0-9_./-]+)", label)
            claims = {"display_name": field(label, "model", pointer + "/td[1]", label)}
            if match:
                # Only formatting whitespace around the namespace separator changes.
                claims["upstream_model"] = field(match[1] + "/" + match[2], "model", pointer + "/td[1]", label)
            if re.search(r"chat|conversation", endpoint, re.I):
                claims["task"] = field("chat", "model", pointer + "/td[2]", endpoint)
            claims["base_url"] = field("https://integrate.api.nvidia.com/v1", "provider",
                "URL + Endpoint", "https://integrate.api.nvidia.com ; POST /v1/chat/completions")
            records.append(evidence(document, "nvidia_hosted", "nvidia", "hosted", pointer, claims))
    if not records:
        raise CatalogError("nvidia_catalog_missing_rows")
    return records


def parse_sf_api(document):
    try:
        body = json.loads(document.body)
    except ValueError:
        raise CatalogError("siliconflow_catalog_not_json") from None
    if (not isinstance(body, dict) or body.get("object") != "list" or not isinstance(body.get("data"), list)
        or any(key in body for key in ("error", "code", "message")) or body.get("has_more") or body.get("next") or body.get("next_page")):
        raise CatalogError("siliconflow_catalog_incomplete_or_invalid")
    records = []
    for index, row in enumerate(body["data"]):
        if not isinstance(row, dict) or row.get("object") != "model" or not isinstance(row.get("id"), str) or not row["id"].strip():
            raise CatalogError("siliconflow_catalog_invalid_row")
        pointer = f"/data/{index}"
        claims = {"upstream_model": field(row["id"], "model", pointer + "/id", row["id"]),
            "task": field("chat", "model", "query.sub_type", "sub_type=chat (documented catalog filter)")}
        records.append(evidence(document, "siliconflow_cn", "siliconflow", "china", pointer, claims,
            catalog_contract_locator=SF_API_DOCS))
    # A documented JSON list with data=[] is a valid empty catalog.
    return records


def parse_sf_docs(document):
    soup = BeautifulSoup(document.body, "html.parser")
    if not soup.find("a", href="https://api.siliconflow.cn/v1") or not soup.find("a", href=SF_WEB):
        raise CatalogError("siliconflow_docs_structure_changed")
    claims = {"base_url": field("https://api.siliconflow.cn/v1", "provider", "a[href=api-base]", "Base Url: https://api.siliconflow.cn/v1"),
              "model_catalog": field(SF_WEB, "provider", "a[href=model-catalog]", "Model ID: obtain from the model catalog")}
    return [evidence(document, "siliconflow_cn_docs", "siliconflow", "china", "configuration", claims)]


def read_catalog(claim, report):
    locator = claim.locator
    credential = None
    expected = {"nvidia_hosted": NVIDIA, "siliconflow_cn": SF_WEB, "siliconflow_cn_docs": SF_DOCS}
    if claim.connector not in expected or locator != expected[claim.connector]:
        raise CatalogError("connector_locator_mismatch", blocked=True)
    if claim.connector == "siliconflow_cn":
        if claim.config.get("credential_env", "SILICONFLOW_CN_CATALOG_API_KEY") != "SILICONFLOW_CN_CATALOG_API_KEY":
            raise CatalogError("unreviewed_credential_reference", blocked=True)
        credential = os.getenv("SILICONFLOW_CN_CATALOG_API_KEY", "")
        if credential:
            locator = claim.config.get("catalog_api_locator")
            if locator != SF_API or claim.config.get("catalog_api_evidence_locator") != SF_API_DOCS:
                raise CatalogError("catalog_api_not_documented", blocked=True)
    try:
        document = fetch_document(locator, credential=credential)
    except CatalogError as error:
        if error.metadata:
            report(error.metadata)
        raise
    report(document.metadata(), document.body)
    if claim.connector == "nvidia_hosted":
        if locator != NVIDIA:
            raise CatalogError("connector_locator_mismatch", blocked=True)
        return parse_nvidia(document)
    if claim.connector == "siliconflow_cn_docs":
        if locator != SF_DOCS:
            raise CatalogError("connector_locator_mismatch", blocked=True)
        return parse_sf_docs(document)
    if claim.connector == "siliconflow_cn":
        if locator == SF_API:
            return parse_sf_api(document)
        if locator != SF_WEB:
            raise CatalogError("connector_locator_mismatch", blocked=True)
        soup = BeautifulSoup(document.body, "html.parser")
        title = " ".join(node.get_text(" ", strip=True) for node in soup.find_all(["h1", "title"]))
        if soup.select('input[type="password"]') or re.search(r"login|sign in|登录", title, re.I):
            raise CatalogError("catalog_login_required", blocked=True)
        raise CatalogError("catalog_interface_unavailable", blocked=True)
    raise CatalogError("connector_not_supported", blocked=True)
