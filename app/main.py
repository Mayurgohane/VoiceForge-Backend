from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, ORJSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from app import __version__
from app.api.container import build_container
from app.api.router import build_api_router, build_ws_router
from app.core.config import get_settings
from app.core.exceptions import NotFoundError, SessionError, UnauthorizedError, VoiceForgeError
from app.core.logging import get_logger, setup_logging
from app.core.security import assert_production_secrets, require_api_key
from app.infrastructure.telemetry import setup_telemetry

logger = get_logger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings)
    assert_production_secrets(settings)
    container = build_container(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.container = container
        await container.startup()
        logger.info(
            "app_started",
            version=__version__,
            env=settings.app_env,
            stt=settings.stt_provider,
            tts=settings.tts_provider,
            llm=settings.llm_provider,
        )
        try:
            yield
        finally:
            await container.shutdown()
            logger.info("app_stopped")

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        default_response_class=ORJSONResponse,
        lifespan=lifespan,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    setup_telemetry(app, settings)

    app.include_router(build_api_router(settings.api_prefix))
    app.include_router(build_ws_router(settings.api_prefix))

    @app.exception_handler(NotFoundError)
    async def not_found_handler(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"code": exc.code, "message": exc.message})

    @app.exception_handler(UnauthorizedError)
    async def unauthorized_handler(_: Request, exc: UnauthorizedError) -> JSONResponse:
        return JSONResponse(status_code=401, content={"code": exc.code, "message": exc.message})

    @app.exception_handler(SessionError)
    async def session_error_handler(_: Request, exc: SessionError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"code": exc.code, "message": exc.message})

    @app.exception_handler(VoiceForgeError)
    async def app_error_handler(_: Request, exc: VoiceForgeError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"code": exc.code, "message": exc.message})

    if settings.prometheus_enabled:
        deps = [Depends(require_api_key)] if settings.require_metrics_auth else []

        @app.get("/metrics", dependencies=deps)
        async def metrics() -> Response:
            return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/")
    async def root() -> dict[str, str]:
        return {
            "app": settings.app_name,
            "version": __version__,
            "docs": "/docs",
            "health": f"{settings.api_prefix}/health",
        }

    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug and not settings.is_production,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()
