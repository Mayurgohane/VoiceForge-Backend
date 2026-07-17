from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.core.config import Settings
from app.core.exceptions import NotFoundError, SessionError
from app.core.logging import get_logger
from app.domain.enums import Channel, SessionStatus
from app.domain.models import TranscriptTurn, VoiceSession, utcnow
from app.infrastructure.database import Database
from app.infrastructure.orm import AuditLogRecord, CallEventRecord, SessionRecord
from app.infrastructure.redis_client import RedisClient
from app.infrastructure.telemetry import SESSIONS_TOTAL

logger = get_logger(__name__)

_TERMINAL = {
    SessionStatus.COMPLETED,
    SessionStatus.FAILED,
    SessionStatus.CANCELLED,
    SessionStatus.WAITING_HUMAN,
}


class SessionManager:
    """Owns session lifecycle across Redis (hot) and Postgres/SQLite (durable)."""

    def __init__(self, settings: Settings, redis: RedisClient, db: Database) -> None:
        self._settings = settings
        self._redis = redis
        self._db = db

    def _key(self, session_id: str) -> str:
        return f"voiceforge:session:{session_id}"

    def _call_key(self, call_sid: str) -> str:
        return f"voiceforge:callsid:{call_sid}"

    async def create(
        self,
        *,
        channel: Channel = Channel.WEBSOCKET,
        caller_id: str | None = None,
        locale: str = "en-US",
        metadata: dict[str, Any] | None = None,
    ) -> VoiceSession:
        metadata = metadata or {}
        session = VoiceSession(
            channel=channel,
            caller_id=caller_id,
            locale=locale,
            metadata=metadata,
            status=SessionStatus.ACTIVE,
        )
        await self._persist(session)
        await self._redis.set_json(
            self._key(session.id),
            session.model_dump(mode="json"),
            ttl=self._settings.session_ttl_seconds,
        )
        call_sid = metadata.get("call_sid") or metadata.get("twilio_call_sid")
        if call_sid:
            await self.bind_call_sid(str(call_sid), session.id)
        SESSIONS_TOTAL.labels(channel=channel.value, status=session.status.value).inc()
        logger.info("session_created", session_id=session.id, channel=channel.value)
        return session

    async def bind_call_sid(self, call_sid: str, session_id: str) -> None:
        if not call_sid:
            return
        await self._redis.client.set(
            self._call_key(call_sid),
            session_id,
            ex=self._settings.session_ttl_seconds,
        )

    async def get_by_call_sid(self, call_sid: str) -> VoiceSession | None:
        if not call_sid:
            return None
        raw = await self._redis.client.get(self._call_key(call_sid))
        if raw:
            session_id = raw.decode() if isinstance(raw, bytes) else str(raw)
            try:
                return await self.get(session_id)
            except NotFoundError:
                pass

        # Redis TTL / restart can drop the mapping — fall back to durable metadata.
        session = await self._find_by_call_sid_db(call_sid)
        if session is not None:
            await self.bind_call_sid(call_sid, session.id)
            logger.info("call_sid_rehydrated_from_db", call_sid=call_sid, session_id=session.id)
        return session

    async def _find_by_call_sid_db(self, call_sid: str) -> VoiceSession | None:
        async with self._db.session() as db:
            result = await db.execute(
                select(SessionRecord)
                .where(SessionRecord.channel == Channel.TWILIO.value)
                .order_by(SessionRecord.created_at.desc())
                .limit(200)
            )
            for row in result.scalars():
                meta = row.metadata_json or {}
                if meta.get("call_sid") == call_sid or meta.get("twilio_call_sid") == call_sid:
                    return self._from_row(row)
        return None

    async def get(self, session_id: str) -> VoiceSession:
        cached = await self._redis.get_json(self._key(session_id))
        if cached:
            # Keep hot session alive for long calls.
            await self._redis.touch(self._key(session_id), self._settings.session_ttl_seconds)
            return VoiceSession.model_validate(cached)

        async with self._db.session() as db:
            row = await db.get(SessionRecord, session_id)
            if row is None:
                raise NotFoundError(f"Session {session_id} not found")
            session = self._from_row(row)
        await self._redis.set_json(
            self._key(session.id),
            session.model_dump(mode="json"),
            ttl=self._settings.session_ttl_seconds,
        )
        return session

    async def save(self, session: VoiceSession) -> VoiceSession:
        session.touch()
        await self._redis.set_json(
            self._key(session.id),
            session.model_dump(mode="json"),
            ttl=self._settings.session_ttl_seconds,
        )
        await self._persist(session)
        return session

    async def add_turn(self, session_id: str, turn: TranscriptTurn) -> VoiceSession:
        session = await self.get(session_id)
        # Allow one assistant turn while entering handoff; otherwise ACTIVE only.
        if session.status == SessionStatus.WAITING_HUMAN and turn.metadata.get("handoff"):
            pass
        elif session.status != SessionStatus.ACTIVE:
            raise SessionError(f"Session {session_id} is not active")
        session.add_turn(turn)
        return await self.save(session)

    async def end(
        self,
        session_id: str,
        *,
        status: SessionStatus = SessionStatus.COMPLETED,
        handoff_reason: str | None = None,
    ) -> VoiceSession:
        session = await self.get(session_id)

        # Idempotent / non-destructive terminal transitions.
        if session.status in _TERMINAL:
            if session.status == SessionStatus.WAITING_HUMAN and status == SessionStatus.COMPLETED:
                # Keep handoff state; disconnect cleanup must not overwrite it.
                return session
            if session.status == status:
                return session
            if session.status in {
                SessionStatus.COMPLETED,
                SessionStatus.FAILED,
                SessionStatus.CANCELLED,
            }:
                return session

        session.status = status
        if handoff_reason:
            session.handoff_reason = handoff_reason
        session.ended_at = utcnow()
        await self.save(session)
        await self._redis.delete(self._key(session_id))
        SESSIONS_TOTAL.labels(channel=session.channel.value, status=status.value).inc()
        logger.info("session_ended", session_id=session_id, status=status.value)
        return session

    async def list_sessions(self, *, limit: int = 50) -> list[VoiceSession]:
        async with self._db.session() as db:
            result = await db.execute(
                select(SessionRecord).order_by(SessionRecord.created_at.desc()).limit(limit)
            )
            rows = result.scalars().all()
            return [self._from_row(row) for row in rows]

    async def record_event(self, session_id: str, event_type: str, payload: dict[str, Any]) -> None:
        # Strip huge audio blobs from durable event log.
        safe_payload = dict(payload)
        if "audio_b64" in safe_payload:
            safe_payload["audio_b64_len"] = len(str(safe_payload.pop("audio_b64")))
        async with self._db.session() as db:
            db.add(
                CallEventRecord(
                    session_id=session_id,
                    event_type=event_type,
                    payload_json=safe_payload,
                )
            )

    async def audit(self, action: str, *, session_id: str | None = None, detail: str = "") -> None:
        async with self._db.session() as db:
            db.add(AuditLogRecord(session_id=session_id, action=action, detail=detail))

    async def _persist(self, session: VoiceSession) -> None:
        async with self._db.session() as db:
            row = await db.get(SessionRecord, session.id)
            payload = {
                "channel": session.channel.value,
                "status": session.status.value,
                "caller_id": session.caller_id,
                "locale": session.locale,
                "handoff_reason": session.handoff_reason,
                "turn_count": len(session.turns),
                "metadata_json": session.metadata,
                "transcript_json": [t.model_dump(mode="json") for t in session.turns],
                "created_at": session.created_at,
                "updated_at": session.updated_at,
                "ended_at": session.ended_at,
            }
            if row is None:
                db.add(SessionRecord(id=session.id, **payload))
            else:
                for key, value in payload.items():
                    setattr(row, key, value)

    @staticmethod
    def _from_row(row: SessionRecord) -> VoiceSession:
        turns = [TranscriptTurn.model_validate(t) for t in (row.transcript_json or [])]
        return VoiceSession(
            id=row.id,
            channel=Channel(row.channel),
            status=SessionStatus(row.status),
            caller_id=row.caller_id,
            locale=row.locale,
            turns=turns,
            metadata=row.metadata_json or {},
            handoff_reason=row.handoff_reason,
            created_at=row.created_at,
            updated_at=row.updated_at,
            ended_at=row.ended_at,
        )
