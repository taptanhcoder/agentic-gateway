import os

from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool


def database_engine():
    return create_engine(
        os.environ["DATABASE_URL"],
        poolclass=NullPool,
        connect_args={"connect_timeout": 2, "options": "-c statement_timeout=2000"},
    )
