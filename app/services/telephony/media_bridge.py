from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.enums import SessionStatus, VoiceEventType
from app.domain.models import VoiceEvent
from app.services.conversation_pipeline import ConversationPipeline
from app.services.session_manager import SessionManager
from app.services.stt.base import TranscriptResult
from app.services.stt.deepgram_live import DeepgramLiveConfig, DeepgramLiveSession
from app.services.telephony.audio import audio_to_twilio_payload, split_mulaw_for_twilio
from app.services.tts.base import TTSProvider

logger = get_logger(__name__)


class TwilioMediaBridge:
    """Bridges Twilio Media Streams ↔ Deepgram Live STT ↔ VoiceForge pipeline ↔ TTS."""

    def __init__(
        self,
        *,
        settings: Settings,
        sessions: SessionManager,
        pipeline: ConversationPipeline,
        tts: TTSProvider,
    ) -> None:
        self._settings = settings
        self._sessions = sessions
        self._pipeline = pipeline
        self._tts = tts

    async def handle(self, websocket: WebSocket, session_id: str) -> None:
        await websocket.accept()
        session = await self._sessions.get(session_id)

        stream_sid: str | None = None
        call_sid: str | None = None
        deepgram: DeepgramLiveSession | None = None
        turn_lock = asyncio.Lock()
        speaking = False
        play_gen = 0
        closed = False
        last_speech_at = time.monotonic()
        recent_finals: list[tuple[str, float]] = []

        async def safe_send(payload: dict[str, Any]) -> bool:
            if closed:
                return False
            try:
                await websocket.send_text(json.dumps(payload))
                return True
            except Exception:  # noqa: BLE001
                logger.debug("twilio_send_failed", session_id=session_id)
                return False

        async def cancel_playback() -> None:
            nonlocal play_gen, speaking
            play_gen += 1
            speaking = False
            if stream_sid:
                await safe_send({"event": "clear", "streamSid": stream_sid})

        async def send_mulaw(mulaw: bytes, gen: int) -> None:
            nonlocal speaking
            speaking = True
            try:
                for chunk in split_mulaw_for_twilio(mulaw, frame_ms=20):
                    if closed or gen != play_gen:
                        return
                    ok = await safe_send(
                        {
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {"payload": base64.b64encode(chunk).decode("ascii")},
                        }
                    )
                    if not ok:
                        return
                    await asyncio.sleep(0.02)
            finally:
                if gen == play_gen:
                    speaking = False

        async def speak(text: str, *, locale: str) -> None:
            nonlocal play_gen
            if not stream_sid or closed:
                return
            try:
                synthesis = await self._tts.synthesize(text, locale=locale)
                mulaw = audio_to_twilio_payload(synthesis.audio, synthesis.content_type)
            except Exception:  # noqa: BLE001
                logger.exception("twilio_tts_failed", session_id=session_id)
                return
            play_gen += 1
            await send_mulaw(mulaw, play_gen)

        def is_duplicate_final(text: str) -> bool:
            now = time.monotonic()
            # Drop identical finals within 1.5s.
            recent_finals[:] = [(t, ts) for t, ts in recent_finals if now - ts < 1.5]
            normalized = " ".join(text.lower().split())
            if any(t == normalized for t, _ in recent_finals):
                return True
            recent_finals.append((normalized, now))
            return False

        async def on_transcript(result: TranscriptResult) -> None:
            nonlocal closed, last_speech_at, speaking
            if closed:
                return

            if not result.is_final:
                # Barge-in only — do not persist every partial to DB.
                if speaking and self._settings.barge_in_enabled and stream_sid:
                    await cancel_playback()
                return

            text = (result.text or "").strip()
            if not text or is_duplicate_final(text):
                return

            last_speech_at = time.monotonic()

            async with turn_lock:
                if closed:
                    return
                # Ignore turns after handoff / completed.
                current = await self._sessions.get(session_id)
                if current.status != SessionStatus.ACTIVE:
                    logger.info(
                        "twilio_ignoring_turn_inactive_session",
                        session_id=session_id,
                        status=current.status.value,
                    )
                    return

                if speaking and stream_sid:
                    await cancel_playback()

                try:
                    events = await self._pipeline.handle_user_text(
                        session_id,
                        text,
                        confidence=result.confidence,
                        stt_ms=result.latency_ms,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("twilio_pipeline_turn_failed", session_id=session_id)
                    return

                await play_events(events, locale=current.locale)

        async def play_events(events: list[VoiceEvent], *, locale: str) -> None:
            nonlocal play_gen
            for event in events:
                if closed:
                    return
                if event.type == VoiceEventType.TTS_AUDIO:
                    audio_b64 = event.payload.get("audio_b64")
                    if not audio_b64 or not stream_sid:
                        continue
                    try:
                        audio = base64.b64decode(audio_b64)
                        mulaw = audio_to_twilio_payload(
                            audio, str(event.payload.get("content_type") or "audio/wav")
                        )
                    except Exception:  # noqa: BLE001
                        logger.exception("twilio_audio_convert_failed", session_id=session_id)
                        continue
                    play_gen += 1
                    await send_mulaw(mulaw, play_gen)
                elif event.type == VoiceEventType.HANDOFF:
                    # Audio already emitted via TTS_AUDIO from pipeline — metadata only.
                    logger.info(
                        "twilio_handoff",
                        session_id=session_id,
                        reason=event.payload.get("reason"),
                    )

        async def silence_watchdog() -> None:
            nonlocal last_speech_at
            timeout = self._settings.silence_timeout_seconds
            if timeout <= 0:
                return
            # Keep nudging on repeated idle windows (cap so we don't loop forever).
            max_nudges = 3
            nudges = 0
            while not closed and nudges < max_nudges:
                await asyncio.sleep(5)
                if closed or speaking:
                    continue
                idle = time.monotonic() - last_speech_at
                if idle < timeout:
                    continue
                nudges += 1
                logger.info(
                    "twilio_silence_timeout",
                    session_id=session_id,
                    idle_s=idle,
                    nudge=nudges,
                )
                if nudges >= max_nudges:
                    await speak(
                        "I haven't heard from you, so I'll end the call now. Goodbye.",
                        locale=session.locale,
                    )
                    last_speech_at = time.monotonic()
                    try:
                        current = await self._sessions.get(session_id)
                        if current.status == SessionStatus.ACTIVE:
                            await self._sessions.end(session_id, status=SessionStatus.COMPLETED)
                    except Exception:  # noqa: BLE001
                        logger.debug("twilio_silence_end_skipped", session_id=session_id)
                    break
                await speak(
                    "Are you still there? I am happy to help with your order or ticket.",
                    locale=session.locale,
                )
                # Reset idle window so the next nudge waits a full timeout again.
                last_speech_at = time.monotonic()

        watchdog_task = asyncio.create_task(silence_watchdog(), name=f"silence-{session_id}")

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("twilio_invalid_json", session_id=session_id)
                    continue

                event = message.get("event")

                if event == "connected":
                    logger.info("twilio_media_connected", session_id=session_id)
                    continue

                if event == "start":
                    start = message.get("start") or {}
                    stream_sid = start.get("streamSid") or message.get("streamSid")
                    call_sid = start.get("callSid")
                    media_format = start.get("mediaFormat") or {}
                    encoding = str(media_format.get("encoding") or "audio/x-mulaw")
                    sample_rate = int(media_format.get("sampleRate") or 8000)

                    session.metadata.update(
                        {
                            "twilio_stream_sid": stream_sid,
                            "twilio_call_sid": call_sid,
                            "media_format": media_format,
                            "custom_parameters": start.get("customParameters") or {},
                        }
                    )
                    await self._sessions.save(session)
                    if call_sid:
                        await self._sessions.bind_call_sid(call_sid, session_id)

                    # Close prior Deepgram if Twilio re-sends start.
                    if deepgram is not None:
                        await deepgram.finalize()
                        await deepgram.close()

                    dg_encoding = (
                        "mulaw" if "mulaw" in encoding or "ulaw" in encoding else "linear16"
                    )
                    deepgram = DeepgramLiveSession(
                        DeepgramLiveConfig(
                            api_key=self._settings.deepgram_api_key,
                            model=self._settings.deepgram_model,
                            language=(session.locale or "en-US").split("-")[0],
                            encoding=dg_encoding,
                            sample_rate=sample_rate,
                            endpointing_ms=self._settings.deepgram_endpointing_ms,
                        )
                    )
                    await deepgram.start(on_transcript)
                    last_speech_at = time.monotonic()
                    logger.info(
                        "twilio_media_started",
                        session_id=session_id,
                        stream_sid=stream_sid,
                        call_sid=call_sid,
                        encoding=dg_encoding,
                        sample_rate=sample_rate,
                    )
                    # Greeting is spoken via TwiML <Say> only — avoid double greeting.
                    continue

                if event == "media":
                    media = message.get("media") or {}
                    if (media.get("track") or "inbound") == "outbound":
                        continue
                    payload_b64 = media.get("payload")
                    if not payload_b64 or deepgram is None:
                        continue
                    try:
                        audio = base64.b64decode(payload_b64)
                        await deepgram.send_audio(audio)
                    except Exception:  # noqa: BLE001
                        logger.warning("twilio_media_frame_skipped", session_id=session_id)
                    continue

                if event == "mark":
                    continue

                if event == "stop":
                    logger.info("twilio_media_stop", session_id=session_id, stream_sid=stream_sid)
                    break

        except WebSocketDisconnect:
            logger.info("twilio_media_disconnected", session_id=session_id)
        except Exception:  # noqa: BLE001
            logger.exception("twilio_media_bridge_error", session_id=session_id)
        finally:
            closed = True
            play_gen += 1
            watchdog_task.cancel()
            try:
                await watchdog_task
            except asyncio.CancelledError:
                pass
            if deepgram is not None:
                try:
                    await deepgram.finalize()
                    await deepgram.close()
                except Exception:  # noqa: BLE001
                    logger.debug("deepgram_cleanup_failed", session_id=session_id)
            try:
                current = await self._sessions.get(session_id)
                if current.status == SessionStatus.ACTIVE:
                    await self._sessions.end(session_id, status=SessionStatus.COMPLETED)
            except Exception:  # noqa: BLE001
                logger.debug("twilio_session_end_skipped", session_id=session_id)
