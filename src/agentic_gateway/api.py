"""Application factory for the runtime foundation."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .config import Settings
from .database import is_ready, make_engine, migration_heads
from .catalog.admin import router as admin_router
from .qualification.admin import router as qualification_router
from .portal.admin import router as portal_router
from .portal.reports import router as reports_router


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(Settings.from_env())
        app.state.engine = engine
        try:
            app.state.migration_heads = migration_heads()
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="AgenticGateway", lifespan=lifespan)
    app.include_router(admin_router)
    app.include_router(qualification_router)
    app.include_router(reports_router)
    app.include_router(portal_router)

    @app.get("/health/live")
    def live():
        return {"status": "live"}

    @app.get("/health/ready")
    def ready():
        if is_ready(app.state.engine, app.state.migration_heads):
            return {"status": "ready"}
        return JSONResponse(status_code=503, content={"status": "not_ready"})

    return app
