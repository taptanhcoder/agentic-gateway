"""GET-only, bounded metadata transport with exact reviewed locators."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib

import httpx

NVIDIA = "https://docs.api.nvidia.com/nim/reference/llm-apis"
SF_WEB = "https://cloud.siliconflow.cn/models"
SF_DOCS = "https://docs.siliconflow.cn/docs/usercases/use-siliconcloud-in-cline"
SF_API_DOCS = "https://docs.siliconflow.cn/docs/api/models-get"
SF_API = "https://api.siliconflow.cn/v1/models?sub_type=chat"
REVIEWED = frozenset({NVIDIA, SF_WEB, SF_DOCS, SF_API})


class CatalogError(Exception):
    def __init__(self, code, blocked=False, metadata=None):
        super().__init__(code)
        self.blocked, self.metadata = blocked, metadata


@dataclass(frozen=True)
class Document:
    locator: str
    body: str
    status: int = 200
    mode: str = "live"
    fetched_at: str = ""

    @property
    def sha256(self):
        return hashlib.sha256(self.body.encode()).hexdigest()

    def metadata(self):
        return {"locator": self.locator, "content_hash": self.sha256, "http_status": self.status,
                "read_at": self.fetched_at, "mode": self.mode}


def fetch_document(locator, credential=None):
    if locator not in REVIEWED:
        raise CatalogError("unreviewed_catalog_locator", blocked=True)
    # Never attach a catalog credential to a documentation, login or redirect host.
    if credential and locator != SF_API:
        raise CatalogError("credential_host_not_allowed", blocked=True)
    headers = {"Accept": "application/json, text/html", "User-Agent": "AgenticGateway-catalog/0.1"}
    if credential:
        headers["Authorization"] = "Bearer " + credential
    read_at = datetime.now(timezone.utc).isoformat()
    metadata = {"locator": locator, "read_at": read_at, "mode": "live"}
    try:
        with httpx.Client(timeout=httpx.Timeout(12, connect=5), follow_redirects=False, trust_env=False) as client:
            with client.stream("GET", locator, headers=headers) as response:
                metadata["http_status"] = response.status_code
                if response.is_redirect:
                    location = response.headers.get("location", "")
                    code = "catalog_login_required" if "login" in location.lower() or "account.siliconflow.cn" in location else "catalog_redirect_unreviewed"
                    raise CatalogError(code, blocked=True, metadata=metadata)
                if response.status_code in {401, 403}:
                    raise CatalogError("catalog_auth_required", blocked=True, metadata=metadata)
                if response.status_code != 200:
                    raise CatalogError(f"catalog_http_{response.status_code}", metadata=metadata)
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 4 * 1024 * 1024:
                        raise CatalogError("catalog_document_too_large", metadata=metadata)
                    chunks.append(chunk)
                body = b"".join(chunks).decode("utf-8", errors="replace")
                if credential:
                    body = body.replace(credential, "[REDACTED]")
                return Document(locator, body, response.status_code, "live", read_at)
    except httpx.RequestError:
        raise CatalogError("catalog_transport_error", metadata=metadata) from None
