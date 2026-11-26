"""Reviewed official locators; seed never overwrites operator configuration."""
from sqlalchemy.dialects.postgresql import insert
from agentic_gateway.catalog.schema import sources
from .transport import NVIDIA, SF_API, SF_API_DOCS, SF_DOCS, SF_WEB

PROVIDERS = [
    {"id":"official/nvidia-hosted", "connector":"nvidia_hosted", "locator":NVIDIA,
     "provider":"nvidia", "catalog_scope":"hosted", "config":{}},
    {"id":"official/siliconflow-cn", "connector":"siliconflow_cn", "locator":SF_WEB,
     "provider":"siliconflow", "catalog_scope":"china", "config":{
         "catalog_api_locator":SF_API, "catalog_api_evidence_locator":SF_API_DOCS,
         "credential_env":"SILICONFLOW_CN_CATALOG_API_KEY"}},
    {"id":"official/siliconflow-cn-docs", "connector":"siliconflow_cn_docs", "locator":SF_DOCS,
     "provider":"siliconflow", "catalog_scope":"china", "config":{}},
]


def seed_providers(engine):
    with engine.begin() as connection:
        for source in PROVIDERS:
            connection.execute(insert(sources).values(**source).on_conflict_do_nothing(index_elements=[sources.c.id]))
