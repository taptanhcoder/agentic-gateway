"""Use the exact same environment and connection limits as the API."""

from alembic import context

from agentic_gateway.config import Settings
from agentic_gateway.database import make_engine


if context.is_offline_mode():
    context.configure(
        url=Settings.from_env().database_url,
        literal_binds=True,
        target_metadata=None,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = make_engine(Settings.from_env())
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=None)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()
