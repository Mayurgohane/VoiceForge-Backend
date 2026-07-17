from __future__ import annotations

from enum import Enum

try:
    from enum import StrEnum
except ImportError:  # Python < 3.11

    class StrEnum(str, Enum):
        """Backport of enum.StrEnum for Python 3.10."""


class SessionStatus(StrEnum):
    CREATED = "created"
    ACTIVE = "active"
    WAITING_HUMAN = "waiting_human"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Channel(StrEnum):
    WEBSOCKET = "websocket"
    TWILIO = "twilio"
    SIMULATION = "simulation"


class TurnRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class VoiceEventType(StrEnum):
    SESSION_STARTED = "session.started"
    SESSION_ENDED = "session.ended"
    AUDIO_CHUNK = "audio.chunk"
    PARTIAL_TRANSCRIPT = "transcript.partial"
    FINAL_TRANSCRIPT = "transcript.final"
    AGENT_THINKING = "agent.thinking"
    AGENT_RESPONSE = "agent.response"
    TOOL_CALL = "tool.call"
    TOOL_RESULT = "tool.result"
    TTS_AUDIO = "tts.audio"
    BARGE_IN = "barge_in"
    HANDOFF = "handoff"
    ERROR = "error"
    METRICS = "metrics"


class HandoffReason(StrEnum):
    LOW_CONFIDENCE = "low_confidence"
    USER_REQUESTED = "user_requested"
    POLICY = "policy"
    TOOL_FAILURE = "tool_failure"
    MAX_TURNS = "max_turns"
