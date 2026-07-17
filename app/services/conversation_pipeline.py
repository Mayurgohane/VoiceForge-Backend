from __future__ import annotations

import base64
import time
from collections.abc import AsyncIterator, Awaitable, Callable

from app.core.config import Settings
from app.core.exceptions import SessionError
from app.core.logging import get_logger
from app.domain.enums import Channel, HandoffReason, SessionStatus, TurnRole, VoiceEventType
from app.domain.models import TranscriptTurn, TurnMetrics, VoiceEvent
from app.infrastructure.telemetry import HANDOFFS_TOTAL, TURN_LATENCY, TURNS_TOTAL
from app.services.agent.runtime import VoiceAgent
from app.services.handoff import HandoffPolicy
from app.services.redaction import PIIRedactor
from app.services.session_manager import SessionManager
from app.services.stt.base import STTProvider
from app.services.telephony.warm_transfer import WarmTransferService
from app.services.tts.base import TTSProvider

logger = get_logger(__name__)

EventCallback = Callable[[VoiceEvent], Awaitable[None]]


class ConversationPipeline:
    """STT → Agent/Tools → TTS pipeline with barge-in and handoff controls."""

    def __init__(
        self,
        *,
        settings: Settings,
        sessions: SessionManager,
        stt: STTProvider,
        tts: TTSProvider,
        agent: VoiceAgent,
        redactor: PIIRedactor,
        handoff: HandoffPolicy,
        warm_transfer: WarmTransferService | None = None,
    ) -> None:
        self._settings = settings
        self._sessions = sessions
        self._stt = stt
        self._tts = tts
        self._agent = agent
        self._redactor = redactor
        self._handoff = handoff
        self._warm_transfer = warm_transfer
        self._barge_in_flags: dict[str, bool] = {}

    def request_barge_in(self, session_id: str) -> None:
        if self._settings.barge_in_enabled:
            self._barge_in_flags[session_id] = True

    def clear_barge_in(self, session_id: str) -> None:
        self._barge_in_flags.pop(session_id, None)

    async def handle_audio_chunk(
        self,
        session_id: str,
        audio: bytes,
        *,
        sequence: int = 0,
        is_final: bool = False,
        emit: EventCallback | None = None,
    ) -> list[VoiceEvent]:
        events: list[VoiceEvent] = []

        async def _emit(event: VoiceEvent) -> None:
            events.append(event)
            if emit:
                await emit(event)

        session = await self._sessions.get(session_id)
        if session.status != SessionStatus.ACTIVE:
            return events

        transcript = await self._stt.transcribe_chunk(
            audio,
            sequence=sequence,
            is_final=is_final,
            locale=session.locale,
            session_id=session_id,
        )
        if transcript is None:
            return events

        if not transcript.is_final:
            await _emit(
                VoiceEvent(
                    type=VoiceEventType.PARTIAL_TRANSCRIPT,
                    session_id=session_id,
                    payload={"text": transcript.text, "confidence": transcript.confidence},
                )
            )
            return events

        await _emit(
            VoiceEvent(
                type=VoiceEventType.FINAL_TRANSCRIPT,
                session_id=session_id,
                payload={"text": transcript.text, "confidence": transcript.confidence},
            )
        )
        turn_events = await self.handle_user_text(
            session_id,
            transcript.text,
            confidence=transcript.confidence,
            stt_ms=transcript.latency_ms,
            emit=emit,
        )
        events.extend(turn_events)
        return events

    async def handle_user_text(
        self,
        session_id: str,
        text: str,
        *,
        confidence: float | None = 0.9,
        stt_ms: int = 0,
        emit: EventCallback | None = None,
    ) -> list[VoiceEvent]:
        started = time.perf_counter()
        events: list[VoiceEvent] = []

        # New user turn owns barge-in state — do not inherit previous playback cancels.
        self.clear_barge_in(session_id)

        raw_text = (text or "").strip()
        if not raw_text:
            return events
        if len(raw_text) > self._settings.max_user_text_chars:
            raw_text = raw_text[: self._settings.max_user_text_chars]
            logger.warning("user_text_truncated", session_id=session_id)

        async def _emit(event: VoiceEvent) -> None:
            events.append(event)
            if emit:
                try:
                    await emit(event)
                except Exception:  # noqa: BLE001
                    logger.debug("emit_failed", session_id=session_id, type=event.type.value)
            # Soft-fail persistence so infra blips don't kill the call.
            try:
                if event.type != VoiceEventType.PARTIAL_TRANSCRIPT:
                    await self._sessions.record_event(session_id, event.type.value, event.payload)
            except Exception:  # noqa: BLE001
                logger.warning("record_event_failed", session_id=session_id, type=event.type.value)

        session = await self._sessions.get(session_id)
        if session.status != SessionStatus.ACTIVE:
            raise SessionError(f"Session {session_id} is not active (status={session.status})")

        safe_text = raw_text
        if self._settings.enable_pii_redaction:
            redaction = self._redactor.redact(raw_text)
            safe_text = redaction.text
            if redaction.redacted:
                labels = sorted({m.split(":", 1)[0] for m in redaction.matches})
                try:
                    await self._sessions.audit(
                        "pii_redacted",
                        session_id=session_id,
                        detail=",".join(labels),
                    )
                except Exception:  # noqa: BLE001
                    logger.debug("audit_failed", session_id=session_id)

        user_turn = TranscriptTurn(
            role=TurnRole.USER,
            content=safe_text,
            confidence=confidence,
            latency_ms=stt_ms,
        )
        session = await self._sessions.add_turn(session_id, user_turn)

        # Use redacted text for handoff keywords + LLM so PII never leaves the boundary.
        decision = self._handoff.evaluate(
            user_text=safe_text,
            confidence=confidence,
            turn_count=len(session.turns),
        )
        if decision.should_handoff:
            return await self._do_handoff(
                session_id,
                decision.reason or HandoffReason.POLICY,
                decision.message or "Transferring you to a human agent.",
                channel=session.channel.value,
                locale=session.locale,
                emit=_emit,
            )

        await _emit(
            VoiceEvent(
                type=VoiceEventType.AGENT_THINKING,
                session_id=session_id,
                payload={"status": "processing"},
            )
        )

        agent_result = await self._agent.run(
            session_id=session_id,
            user_text=safe_text,
            history=session.turns,
            locale=session.locale,
            caller_id=session.caller_id,
            channel=session.channel.value,
        )
        TURN_LATENCY.labels(stage="llm").observe(agent_result.latency_ms / 1000)

        for tool in agent_result.tool_results:
            await _emit(
                VoiceEvent(
                    type=VoiceEventType.TOOL_RESULT,
                    session_id=session_id,
                    payload=tool,
                )
            )

        if agent_result.should_handoff:
            return await self._do_handoff(
                session_id,
                HandoffReason(agent_result.handoff_reason or "policy"),
                agent_result.text,
                channel=session.channel.value,
                locale=session.locale,
                emit=_emit,
            )

        post_decision = self._handoff.evaluate(
            user_text=safe_text,
            confidence=agent_result.confidence,
            turn_count=len(session.turns),
            tool_failed=any(not t.get("success", True) for t in agent_result.tool_results),
        )
        if post_decision.should_handoff:
            return await self._do_handoff(
                session_id,
                post_decision.reason or HandoffReason.LOW_CONFIDENCE,
                post_decision.message or agent_result.text,
                channel=session.channel.value,
                locale=session.locale,
                emit=_emit,
            )

        assistant_turn = TranscriptTurn(
            role=TurnRole.ASSISTANT,
            content=agent_result.text,
            confidence=agent_result.confidence,
            latency_ms=agent_result.latency_ms,
            metadata={"tools": agent_result.tool_results},
        )
        await self._sessions.add_turn(session_id, assistant_turn)

        await _emit(
            VoiceEvent(
                type=VoiceEventType.AGENT_RESPONSE,
                session_id=session_id,
                payload={
                    "text": agent_result.text,
                    "confidence": agent_result.confidence,
                },
            )
        )

        # Drop TTS early if user already interrupted during LLM.
        if self._barge_in_flags.get(session_id):
            self.clear_barge_in(session_id)
            await _emit(
                VoiceEvent(
                    type=VoiceEventType.BARGE_IN,
                    session_id=session_id,
                    payload={"dropped_tts": True, "stage": "pre_tts"},
                )
            )
            return events

        tts_started = time.perf_counter()
        synthesis = await self._tts.synthesize(agent_result.text, locale=session.locale)
        tts_ms = int((time.perf_counter() - tts_started) * 1000)
        TURN_LATENCY.labels(stage="tts").observe(tts_ms / 1000)

        barged = self._barge_in_flags.pop(session_id, False)
        if barged:
            await _emit(
                VoiceEvent(
                    type=VoiceEventType.BARGE_IN,
                    session_id=session_id,
                    payload={"dropped_tts": True, "stage": "post_tts"},
                )
            )
        else:
            await _emit(
                VoiceEvent(
                    type=VoiceEventType.TTS_AUDIO,
                    session_id=session_id,
                    payload={
                        "content_type": synthesis.content_type,
                        "audio_b64": base64.b64encode(synthesis.audio).decode(),
                        "transcript": synthesis.transcript,
                    },
                )
            )

        total_ms = int((time.perf_counter() - started) * 1000)
        metrics = TurnMetrics(
            session_id=session_id,
            stt_ms=stt_ms,
            llm_ms=agent_result.latency_ms,
            tts_ms=tts_ms,
            total_ms=total_ms,
            barged_in=barged,
        )
        TURN_LATENCY.labels(stage="total").observe(total_ms / 1000)
        TURNS_TOTAL.labels(channel=session.channel.value, outcome="ok").inc()
        await _emit(
            VoiceEvent(
                type=VoiceEventType.METRICS,
                session_id=session_id,
                payload=metrics.model_dump(),
            )
        )

        if total_ms > self._settings.max_turn_latency_ms:
            logger.warning(
                "turn_latency_sla_miss",
                session_id=session_id,
                total_ms=total_ms,
                sla_ms=self._settings.max_turn_latency_ms,
            )

        return events

    async def _do_handoff(
        self,
        session_id: str,
        reason: HandoffReason,
        message: str,
        *,
        channel: str,
        locale: str,
        emit: Callable[[VoiceEvent], Awaitable[None]],
    ) -> list[VoiceEvent]:
        events: list[VoiceEvent] = []

        async def _capture(event: VoiceEvent) -> None:
            events.append(event)
            await emit(event)

        session = await self._sessions.get(session_id)
        await self._sessions.add_turn(
            session_id,
            TranscriptTurn(role=TurnRole.ASSISTANT, content=message, metadata={"handoff": True}),
        )
        await self._sessions.end(
            session_id,
            status=SessionStatus.WAITING_HUMAN,
            handoff_reason=reason.value,
        )
        HANDOFFS_TOTAL.labels(reason=reason.value).inc()
        TURNS_TOTAL.labels(channel=channel, outcome="handoff").inc()

        transfer_payload: dict = {"reason": reason.value, "message": message}
        if (
            channel == Channel.TWILIO.value
            and self._warm_transfer is not None
            and self._warm_transfer.enabled
        ):
            # Build short summary from recent turns for agent whisper.
            summary_bits = [t.content for t in session.turns[-6:]]
            summary = " | ".join(summary_bits) if summary_bits else message
            # Refresh session after end for metadata/call_sid.
            ended = await self._sessions.get(session_id)
            result = await self._warm_transfer.start(ended, reason=reason.value, summary=summary)
            transfer_payload["warm_transfer"] = {
                "success": result.success,
                "conference": result.conference_name,
                "agent_call_sid": result.agent_call_sid,
                "error": result.error,
            }
            if result.success:
                message = (
                    "I'm connecting you to a specialist now. Please hold for a moment."
                )

        await _capture(
            VoiceEvent(
                type=VoiceEventType.HANDOFF,
                session_id=session_id,
                payload=transfer_payload,
            )
        )
        synthesis = await self._tts.synthesize(message, locale=locale)
        await _capture(
            VoiceEvent(
                type=VoiceEventType.TTS_AUDIO,
                session_id=session_id,
                payload={
                    "content_type": synthesis.content_type,
                    "audio_b64": base64.b64encode(synthesis.audio).decode(),
                    "transcript": message,
                },
            )
        )
        return events

    async def stream_events(
        self,
        session_id: str,
        text: str,
    ) -> AsyncIterator[VoiceEvent]:
        queue: list[VoiceEvent] = []

        async def emit(event: VoiceEvent) -> None:
            queue.append(event)

        await self.handle_user_text(session_id, text, emit=emit)
        for event in queue:
            yield event
