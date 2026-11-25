from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .db import database_engine


def create_app():
    engine = database_engine()

    @asynccontextmanager
    async def lifespan(app):
        yield
        engine.dispose()

    app = FastAPI(title="AgenticGateway", lifespan=lifespan)

    @app.get("/health/live")
    def live():
        return {"status": "live"}

    @app.get("/health/ready")
    def ready():
        try:
            with engine.connect() as connection:
                revisions = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalars().all()
            if revisions == ["0001"]:
                return {"status": "ready"}
        except SQLAlchemyError:
            pass
        return JSONResponse({"status": "unavailable"}, status_code=503)

    return app
