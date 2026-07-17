from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(slots=True)
class TranscriptResult:
    text: str
    is_final: bool
    confidence: float
    latency_ms: int = 0
    speech_final: bool = False


class STTProvider(ABC):
    """Streaming speech-to-text provider contract."""

    name: str

    @abstractmethod
    async def transcribe_chunk(
        self,
        audio: bytes,
        *,
        sequence: int,
        is_final: bool = False,
        locale: str = "en-US",
        session_id: str | None = None,
    ) -> TranscriptResult | None:
        raise NotImplementedError

    async def close(self) -> None:
        return None
