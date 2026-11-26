"""Environment configuration shared by API and migrations."""

import os
from dataclasses import dataclass, field

from sqlalchemy import URL


@dataclass(frozen=True)
class Settings:
    host: str = "db"
    port: int = 5432
    database: str = "agentic_gateway"
    username: str = "gateway"
    password: str = field(default="", repr=False)

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            host=os.getenv("DB_HOST", "db"),
            port=int(os.getenv("DB_PORT", "5432")),
            database=os.getenv("POSTGRES_DB", "agentic_gateway"),
            username=os.getenv("POSTGRES_USER", "gateway"),
            password=os.getenv("POSTGRES_PASSWORD", ""),
        )

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg", username=self.username, password=self.password,
            host=self.host, port=self.port, database=self.database,
        )
