from alembic import context

from agentic_gateway.db import database_engine

engine = database_engine()
try:
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
finally:
    engine.dispose()
