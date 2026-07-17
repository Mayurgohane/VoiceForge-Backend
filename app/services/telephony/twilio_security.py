from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from urllib.parse import urlencode

from app.core.config import Settings
from app.core.exceptions import UnauthorizedError
from app.core.logging import get_logger
from app.infrastructure.redis_client import RedisClient

logger = get_logger(__name__)


def validate_twilio_signature(
    *,
    auth_token: str,
    url: str,
    params: dict[str, str],
    signature: str | None,
) -> bool:
    """Validate X-Twilio-Signature for form-encoded webhooks."""
    if not auth_token or not signature:
        return False

    import base64

    s = url
    for key in sorted(params.keys()):
        s += key + params[key]
    digest = hmac.new(auth_token.encode("utf-8"), s.encode("utf-8"), hashlib.sha1).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, signature)


def twilio_signature_candidates(request_url: str, public_base_url: str, path: str, query: str) -> list[str]:
    """Build plausible public URLs Twilio may have signed (proxy / ngrok safe)."""
    candidates: list[str] = []
    if public_base_url:
        base = public_base_url.rstrip("/")
        built = f"{base}{path}"
        if query:
            built = f"{built}?{query}"
        candidates.append(built)
        if built.startswith("https://"):
            candidates.append("http://" + built[len("https://") :])
        elif built.startswith("http://"):
            candidates.append("https://" + built[len("http://") :])
    if request_url:
        candidates.append(request_url)
    seen: set[str] = set()
    out: list[str] = []
    for url in candidates:
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _sign(settings: Settings, session_id: str, expires: int, nonce: str) -> str:
    payload = f"{session_id}:{expires}:{nonce}"
    return hmac.new(
        settings.api_key.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _nonce_key(session_id: str, nonce: str) -> str:
    return f"voiceforge:stream_nonce:{session_id}:{nonce}"


def _bound_key(session_id: str) -> str:
    return f"voiceforge:stream_bound:{session_id}"


def _binding_value(expires: int, nonce: str) -> str:
    return f"{expires}.{nonce}"


async def create_stream_token(
    settings: Settings,
    redis: RedisClient,
    session_id: str,
    *,
    ttl_seconds: int | None = None,
) -> str:
    """Create a signed Media Stream token and store its nonce in Redis."""
    ttl = ttl_seconds if ttl_seconds is not None else settings.stream_token_ttl_seconds
    expires = int(time.time()) + ttl
    nonce = secrets.token_urlsafe(16)
    sig = _sign(settings, session_id, expires, nonce)
    # Store nonce as unused (value=1). First connect consumes it and binds the session.
    await redis.setnx(_nonce_key(session_id, nonce), "1", ex=ttl)
    # Always overwrite if somehow collided (extremely unlikely).
    await redis.client.set(_nonce_key(session_id, nonce), "1", ex=ttl)
    return f"{expires}.{nonce}.{sig}"


async def verify_stream_token(
    settings: Settings,
    redis: RedisClient,
    session_id: str,
    token: str | None,
) -> None:
    """Validate HMAC; first use binds the session, same token may reconnect until expiry."""
    if not token:
        raise UnauthorizedError("Missing stream token")
    parts = token.split(".")
    if len(parts) != 3:
        raise UnauthorizedError("Invalid stream token format")
    expires_s, nonce, sig = parts
    try:
        expires = int(expires_s)
    except ValueError as exc:
        raise UnauthorizedError("Invalid stream token") from exc
    now = int(time.time())
    if expires < now:
        raise UnauthorizedError("Stream token expired")

    expected = _sign(settings, session_id, expires, nonce)
    if not hmac.compare_digest(expected, sig):
        raise UnauthorizedError("Invalid stream token")

    binding = _binding_value(expires, nonce)
    remaining = max(1, expires - now)

    # Reconnect path: same token already bound to this session.
    existing = await redis.client.get(_bound_key(session_id))
    if existing is not None:
        bound = existing.decode() if isinstance(existing, bytes) else str(existing)
        if hmac.compare_digest(bound, binding):
            logger.info("stream_token_reconnect_allowed", session_id=session_id)
            return
        logger.warning("stream_token_bound_mismatch", session_id=session_id)
        raise UnauthorizedError("Stream token does not match active binding")

    # First connect: consume unused nonce and bind session → token.
    consumed = await redis.getdel(_nonce_key(session_id, nonce))
    if consumed is None:
        logger.warning("stream_token_unknown_or_spent", session_id=session_id)
        raise UnauthorizedError("Stream token already used or unknown")

    await redis.client.set(_bound_key(session_id), binding, ex=remaining)
    logger.info("stream_token_bound", session_id=session_id)


async def build_media_stream_url(
    settings: Settings,
    redis: RedisClient,
    session_id: str,
) -> str:
    token = await create_stream_token(settings, redis, session_id)
    base = settings.public_base_url.rstrip("/")
    ws_base = base.replace("https://", "wss://").replace("http://", "ws://")
    query = urlencode({"token": token})
    return f"{ws_base}{settings.api_prefix}/twilio/media-stream/{session_id}?{query}"
