"""Idempotent seed; does not reset source generations or published pointers."""

from agentic_gateway.config import Settings
from agentic_gateway.database import make_engine
from .registry import register_source
from agentic_gateway.providers.catalog.seeds import seed_providers


def main():
    engine = make_engine(Settings.from_env())
    try:
        register_source(engine, "fixture/catalog-a", "catalog-a")
        seed_providers(engine)
        print('{"event":"source_seeded","source_id":"fixture/catalog-a"}', flush=True)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
