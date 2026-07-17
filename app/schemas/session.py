from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.domain.enums import Channel, SessionStatus


class CreateSessionRequest(BaseModel):
    channel: Channel = Channel.WEBSOCKET
    caller_id: str | None = None
    locale: str = "en-US"
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionResponse(BaseModel):
    id: str
    channel: Channel
    status: SessionStatus
    caller_id: str | None = None
    locale: str
    turn_count: int
    created_at: datetime
    updated_at: datetime
    ended_at: datetime | None = None
    handoff_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionListResponse(BaseModel):
    items: list[SessionResponse]
    total: int


class HealthResponse(BaseModel):
    status: str
    app: str
    version: str
    environment: str
    checks: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None
