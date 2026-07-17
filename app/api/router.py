from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import health, sessions, twilio, voice_ws


def build_api_router(api_prefix: str) -> APIRouter:
    router = APIRouter(prefix=api_prefix)
    router.include_router(health.router)
    router.include_router(sessions.router)
    router.include_router(twilio.router)
    return router


def build_ws_router(api_prefix: str) -> APIRouter:
    router = APIRouter(prefix=api_prefix)
    router.include_router(voice_ws.router)
    return router
