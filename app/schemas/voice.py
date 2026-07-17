from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class ClientAudioMessage(BaseModel):
    type: Literal["audio.chunk"] = "audio.chunk"
    data: str
    sequence: int = 0
    is_final: bool = False


class ClientControlMessage(BaseModel):
    type: Literal["control"]
    action: Literal["end", "barge_in", "ping", "text_turn"]
    text: str | None = None


class ServerEventMessage(BaseModel):
    type: str
    session_id: str
    payload: dict[str, Any] = Field(default_factory=dict)


class TextTurnRequest(BaseModel):
    """Dev/testing endpoint: drive the agent without audio."""

    text: str = Field(min_length=1, max_length=4000)

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("text must not be empty")
        return cleaned
