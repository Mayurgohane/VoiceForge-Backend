from __future__ import annotations

import asyncio
import base64
import json

from fastapi import APIRouter, Header, Query, WebSocket, WebSocketDisconnect, status

from app.api.container import AppContainer
from app.core.logging import get_logger
from app.core.security import assert_api_key
from app.domain.enums import SessionStatus, VoiceEventType
from app.domain.models import VoiceEvent

logger = get_logger(__name__)
router = APIRouter(tags=["voice"])


@router.websocket("/ws/voice/{session_id}")
async def voice_websocket(
    websocket: WebSocket,
    session_id: str,
    api_key: str | None = Query(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    container: AppContainer = websocket.app.state.container
    try:
        # Prefer header over query string (query may leak via logs/proxies).
        assert_api_key(x_api_key or api_key, container.settings)
    except Exception:  # noqa: BLE001
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    await container.sessions.get(session_id)
    closed = False
    # Serialize turns (matches Twilio media bridge) so back-to-back audio/text
    # cannot interleave LLM/TTS. Barge-in/ping bypass the lock so interrupts stay fast.
    turn_lock = asyncio.Lock()

    async def emit(event: VoiceEvent) -> None:
        if closed:
            return
        try:
            await websocket.send_json(event.model_dump(mode="json"))
        except Exception:  # noqa: BLE001
            logger.debug("ws_emit_failed", session_id=session_id)

    await emit(
        VoiceEvent(
            type=VoiceEventType.SESSION_STARTED,
            session_id=session_id,
            payload={"message": "Voice session connected"},
        )
    )

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break

            raw_text = message.get("text")
            raw_bytes = message.get("bytes")

            try:
                if raw_bytes is not None:
                    async with turn_lock:
                        await container.pipeline.handle_audio_chunk(
                            session_id,
                            raw_bytes,
                            is_final=True,
                            emit=emit,
                        )
                    continue

                if not raw_text:
                    continue

                try:
                    payload = json.loads(raw_text)
                except json.JSONDecodeError:
                    async with turn_lock:
                        await container.pipeline.handle_user_text(session_id, raw_text, emit=emit)
                    continue

                msg_type = payload.get("type")
                if msg_type == "audio.chunk":
                    audio = base64.b64decode(payload.get("data", "") or "")
                    async with turn_lock:
                        await container.pipeline.handle_audio_chunk(
                            session_id,
                            audio,
                            sequence=int(payload.get("sequence") or 0),
                            is_final=bool(payload.get("is_final", False)),
                            emit=emit,
                        )
                elif msg_type == "control":
                    action = payload.get("action")
                    if action == "end":
                        async with turn_lock:
                            await container.sessions.end(session_id)
                        await emit(
                            VoiceEvent(
                                type=VoiceEventType.SESSION_ENDED,
                                session_id=session_id,
                                payload={},
                            )
                        )
                        break
                    if action == "barge_in":
                        container.pipeline.request_barge_in(session_id)
                        await emit(
                            VoiceEvent(
                                type=VoiceEventType.BARGE_IN,
                                session_id=session_id,
                                payload={"requested": True},
                            )
                        )
                    elif action == "ping":
                        await websocket.send_json({"type": "pong", "session_id": session_id})
                    elif action == "text_turn":
                        text = str(payload.get("text") or "")
                        async with turn_lock:
                            await container.pipeline.handle_user_text(session_id, text, emit=emit)
                else:
                    await emit(
                        VoiceEvent(
                            type=VoiceEventType.ERROR,
                            session_id=session_id,
                            payload={"message": f"Unsupported message type: {msg_type}"},
                        )
                    )
            except Exception as exc:  # noqa: BLE001
                logger.exception("websocket_message_error", session_id=session_id, error=str(exc))
                await emit(
                    VoiceEvent(
                        type=VoiceEventType.ERROR,
                        session_id=session_id,
                        payload={"message": str(exc)},
                    )
                )
    except WebSocketDisconnect:
        logger.info("websocket_disconnected", session_id=session_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("websocket_error", session_id=session_id, error=str(exc))
    finally:
        closed = True
        container.pipeline.clear_barge_in(session_id)
        try:
            current = await container.sessions.get(session_id)
            if current.status == SessionStatus.ACTIVE:
                await container.sessions.end(session_id, status=SessionStatus.COMPLETED)
        except Exception:  # noqa: BLE001
            logger.debug("ws_session_end_skipped", session_id=session_id)
        logger.info("websocket_closed", session_id=session_id)
