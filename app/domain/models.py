from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from app.domain.enums import Channel, SessionStatus, TurnRole, VoiceEventType


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class TranscriptTurn(BaseModel):
    id: str = Field(default_factory=new_id)
    role: TurnRole
    content: str
    confidence: float | None = None
    latency_ms: int | None = None
    tool_name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)


class VoiceSession(BaseModel):
    id: str = Field(default_factory=new_id)
    channel: Channel = Channel.WEBSOCKET
    status: SessionStatus = SessionStatus.CREATED
    caller_id: str | None = None
    locale: str = "en-US"
    turns: list[TranscriptTurn] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    handoff_reason: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    ended_at: datetime | None = None

    def touch(self) -> None:
        self.updated_at = utcnow()

    def add_turn(self, turn: TranscriptTurn) -> None:
        self.turns.append(turn)
        self.touch()


class VoiceEvent(BaseModel):
    type: VoiceEventType
    session_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    ts: datetime = Field(default_factory=utcnow)


class TurnMetrics(BaseModel):
    session_id: str
    stt_ms: int = 0
    llm_ms: int = 0
    tts_ms: int = 0
    tool_ms: int = 0
    total_ms: int = 0
    barged_in: bool = False
