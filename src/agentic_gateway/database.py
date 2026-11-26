"""Bounded database access; no connection is retained by health requests."""

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from .config import Settings


def make_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url,
        connect_args={
            "connect_timeout": 2,
            "options": "-c statement_timeout=2000 -c lock_timeout=2000",
        },
        pool_size=2, max_overflow=0, pool_timeout=2,
        pool_pre_ping=True, hide_parameters=True,
    )


def migration_heads() -> frozenset[str]:
    config = Config()
    config.set_main_option("script_location", "agentic_gateway:migrations")
    heads = frozenset(ScriptDirectory.from_config(config).get_heads())
    if not heads:
        raise RuntimeError("Image contains no migration head")
    return heads


def is_ready(engine: Engine, expected_heads: frozenset[str]) -> bool:
    try:
        with engine.connect() as connection:
            revisions = frozenset(
                connection.execute(text("SELECT version_num FROM alembic_version")).scalars()
            )
            return revisions == expected_heads
    except SQLAlchemyError:
        # Do not expose exception details, credentials, or connection URLs.
        return False
