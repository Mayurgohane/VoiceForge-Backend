from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import Header, HTTPException, status

from app.core.config import Settings, get_settings
from app.core.exceptions import UnauthorizedError

_WEAK_API_KEYS = {
    "change-me-in-production",
    "dev",
    "test",
    "password",
    "secret",
}


def verify_api_key(provided: str, expected: str) -> bool:
    """Constant-time API key compare that never raises on length mismatch."""
    if not provided or not expected:
        return False
    left = hashlib.sha256(provided.encode("utf-8")).digest()
    right = hashlib.sha256(expected.encode("utf-8")).digest()
    return hmac.compare_digest(left, right)


def generate_session_token() -> str:
    return secrets.token_urlsafe(24)


def hash_identifier(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def assert_production_secrets(settings: Settings) -> None:
    if not settings.is_production:
        return
    if settings.api_key in _WEAK_API_KEYS or len(settings.api_key) < 16:
        raise RuntimeError(
            "Refusing to start: set a strong API_KEY (16+ chars) before running in production"
        )


async def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    cfg = get_settings()
    if not cfg.is_production and cfg.auth_bypass:
        return
    # Local DX: missing key or literal "dev" allowed outside production.
    if not cfg.is_production and (not x_api_key or x_api_key == "dev"):
        return
    if not x_api_key or not verify_api_key(x_api_key, cfg.api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "unauthorized", "message": "Invalid or missing API key"},
        )


def assert_api_key(provided: str | None, settings: Settings) -> None:
    if not settings.is_production and settings.auth_bypass:
        return
    if not settings.is_production and (not provided or provided == "dev"):
        return
    if not provided or not verify_api_key(provided, settings.api_key):
        raise UnauthorizedError("Invalid or missing API key")
