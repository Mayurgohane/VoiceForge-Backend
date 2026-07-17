from __future__ import annotations

import html

from fastapi import APIRouter, Form, Header, Query, Request, Response, WebSocket, status
from fastapi.exceptions import HTTPException
from fastapi.responses import PlainTextResponse

from app.api.deps import get_container
from app.core.exceptions import UnauthorizedError
from app.core.logging import get_logger
from app.domain.enums import Channel, SessionStatus
from app.services.telephony.media_bridge import TwilioMediaBridge
from app.services.telephony.twilio_security import (
    build_media_stream_url,
    twilio_signature_candidates,
    validate_twilio_signature,
    verify_stream_token,
)
from app.services.telephony.warm_transfer import agent_whisper_twiml, caller_conference_twiml

logger = get_logger(__name__)
router = APIRouter(prefix="/twilio", tags=["twilio"])


def _require_twilio_or_dev(request: Request, form_data: dict[str, str], signature: str | None) -> None:
    container = get_container(request)
    settings = container.settings

    if not settings.twilio_auth_token:
        if settings.app_env == "development":
            logger.warning("twilio_signature_skipped_dev_mode")
            return
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="TWILIO_AUTH_TOKEN is not configured",
        )

    candidates = twilio_signature_candidates(
        request_url=str(request.url),
        public_base_url=settings.public_base_url,
        path=request.url.path,
        query=request.url.query,
    )
    ok = any(
        validate_twilio_signature(
            auth_token=settings.twilio_auth_token,
            url=url,
            params=form_data,
            signature=signature,
        )
        for url in candidates
    )
    if not ok:
        logger.warning("twilio_signature_failed", candidates=candidates)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Twilio signature",
        )


@router.post("/voice")
async def incoming_call(
    request: Request,
    From: str = Form(default="unknown"),
    To: str = Form(default=""),
    CallSid: str = Form(default=""),
    x_twilio_signature: str | None = Header(default=None, alias="X-Twilio-Signature"),
) -> Response:
    """Twilio Voice webhook — answers call and opens a bidirectional Media Stream."""
    form = dict(await request.form())
    form_data = {k: str(v) for k, v in form.items()}
    _require_twilio_or_dev(request, form_data, x_twilio_signature)

    container = get_container(request)
    if not container.settings.deepgram_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DEEPGRAM_API_KEY is required for Twilio Media Streams",
        )

    call_sid = CallSid or form_data.get("CallSid", "")
    session = await container.sessions.create(
        channel=Channel.TWILIO,
        caller_id=From or form_data.get("From", "unknown"),
        metadata={
            "provider": "twilio",
            "call_sid": call_sid,
            "to": To or form_data.get("To"),
        },
    )
    stream_url = await build_media_stream_url(container.settings, container.redis, session.id)
    greeting = html.escape(container.settings.twilio_greeting, quote=True)

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="Polly.Joanna">{greeting}</Say>
  <Connect>
    <Stream url="{html.escape(stream_url, quote=True)}">
      <Parameter name="session_id" value="{html.escape(session.id, quote=True)}" />
      <Parameter name="call_sid" value="{html.escape(call_sid, quote=True)}" />
    </Stream>
  </Connect>
</Response>"""
    logger.info(
        "twilio_voice_answered",
        session_id=session.id,
        call_sid=call_sid,
        from_number=From,
    )
    return PlainTextResponse(content=twiml, media_type="text/xml")


@router.post("/status")
async def call_status(
    request: Request,
    CallSid: str = Form(default=""),
    CallStatus: str = Form(default=""),
    x_twilio_signature: str | None = Header(default=None, alias="X-Twilio-Signature"),
) -> Response:
    """Twilio status callback — ends session on terminal call states."""
    form = dict(await request.form())
    form_data = {k: str(v) for k, v in form.items()}
    _require_twilio_or_dev(request, form_data, x_twilio_signature)

    container = get_container(request)
    call_sid = CallSid or form_data.get("CallSid", "")
    call_status = (CallStatus or form_data.get("CallStatus", "")).lower()
    logger.info("twilio_call_status", call_sid=call_sid, status=call_status)

    if call_sid and call_status in {"completed", "busy", "failed", "no-answer", "canceled", "cancelled"}:
        session = await container.sessions.get_by_call_sid(call_sid)
        if session and session.status == SessionStatus.ACTIVE:
            await container.sessions.end(session.id, status=SessionStatus.COMPLETED)
            logger.info("twilio_session_ended_via_status", session_id=session.id, call_sid=call_sid)

    return PlainTextResponse("ok")


@router.post("/transfer/{session_id}/caller")
async def transfer_caller_twiml(
    session_id: str,
    request: Request,
    x_twilio_signature: str | None = Header(default=None, alias="X-Twilio-Signature"),
) -> Response:
    """TwiML: park caller in conference while agent is whispered context."""
    form = dict(await request.form())
    form_data = {k: str(v) for k, v in form.items()}
    _require_twilio_or_dev(request, form_data, x_twilio_signature)
    return PlainTextResponse(caller_conference_twiml(session_id), media_type="text/xml")


@router.post("/transfer/{session_id}/agent")
async def transfer_agent_twiml(
    session_id: str,
    request: Request,
    x_twilio_signature: str | None = Header(default=None, alias="X-Twilio-Signature"),
) -> Response:
    """TwiML: whisper transfer summary to agent, then join conference."""
    form = dict(await request.form())
    form_data = {k: str(v) for k, v in form.items()}
    _require_twilio_or_dev(request, form_data, x_twilio_signature)

    container = get_container(request)
    ctx = await container.warm_transfer.get_transfer_context(session_id) or {}
    summary = str(ctx.get("summary") or "Customer requested a human agent.")
    reason = str(ctx.get("reason") or "handoff")
    return PlainTextResponse(
        agent_whisper_twiml(session_id, summary=summary, reason=reason),
        media_type="text/xml",
    )


@router.websocket("/media-stream/{session_id}")
async def twilio_media_stream(
    websocket: WebSocket,
    session_id: str,
    token: str | None = Query(default=None),
) -> None:
    """Twilio Media Streams WebSocket with one-time stream token."""
    container = websocket.app.state.container
    try:
        await verify_stream_token(container.settings, container.redis, session_id, token)
    except UnauthorizedError:
        await websocket.close(code=1008)
        return

    if not container.settings.deepgram_api_key:
        logger.error("deepgram_required_for_media_stream", session_id=session_id)
        await websocket.close(code=1013)
        return

    bridge = TwilioMediaBridge(
        settings=container.settings,
        sessions=container.sessions,
        pipeline=container.pipeline,
        tts=container.tts,
    )
    await bridge.handle(websocket, session_id)
