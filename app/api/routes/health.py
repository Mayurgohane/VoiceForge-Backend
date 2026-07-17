from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.api.deps import get_container
from app.core.security import require_api_key
from app.schemas.session import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    container = get_container(request)
    db_ok = await container.db.healthcheck()
    redis_ok = await container.redis.healthcheck()
    redis_stats = await container.redis.stats()
    pool = await container.db.pool_status()

    # Production/staging: memory Redis is not ready.
    redis_mode_ok = True
    if container.settings.app_env in {"production", "staging"} and container.redis.using_memory:
        redis_mode_ok = False

    status = "ok" if db_ok and redis_ok and redis_mode_ok else "degraded"
    return HealthResponse(
        status=status,
        app=container.settings.app_name,
        version=__version__,
        environment=container.settings.app_env,
        checks={
            "database": "ok" if db_ok else "fail",
            "database_revision": str(container.db.schema_revision or "unknown"),
            "database_pool": pool,
            "redis": "ok" if redis_ok else "fail",
            "redis_mode": "memory" if container.redis.using_memory else "redis",
            "redis_stats": redis_stats,
            "stt": container.stt.name,
            "tts": container.tts.name,
            "llm": container.settings.llm_provider,
        },
    )


@router.get("/ready", response_model=HealthResponse, dependencies=[Depends(require_api_key)])
async def ready(request: Request) -> JSONResponse | HealthResponse:
    body = await health(request)
    if body.status != "ok" and request.app.state.container.settings.app_env in {
        "production",
        "staging",
    }:
        return JSONResponse(status_code=503, content=body.model_dump())
    return body
