import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.errors import install_error_handlers
from .api.routes import backends, health, keys, models, requests, routing
from .config import Settings, get_settings
from .repository import Database

LOG_FORMAT = '{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}'


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(level=settings.log_level.upper(), format=LOG_FORMAT)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    if settings.is_production and not settings.auth_required:
        raise RuntimeError("生产环境必须开启 auth_required")
    database = Database(settings)
    app.state.database = database
    logging.getLogger("app.startup").info(
        "app=%s env=%s host=%s port=%s",
        settings.app_name,
        settings.app_env,
        settings.server_host,
        settings.server_port,
    )
    try:
        yield
    finally:
        await database.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved)

    app = FastAPI(
        title=resolved.app_name,
        version=resolved.app_version,
        lifespan=lifespan,
        root_path=resolved.server_root_path,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )
    app.state.settings = resolved

    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-API-Key", "X-Request-Id"],
    )

    install_error_handlers(app)

    app.include_router(health.router)
    app.include_router(models.router)
    app.include_router(backends.router)
    app.include_router(keys.router)
    app.include_router(requests.router)
    app.include_router(routing.router)

    return app


app = create_app()
