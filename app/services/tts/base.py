from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(slots=True)
class SynthesisResult:
    audio: bytes
    content_type: str
    latency_ms: int
    transcript: str


class TTSProvider(ABC):
    name: str

    @abstractmethod
    async def synthesize(self, text: str, *, locale: str = "en-US") -> SynthesisResult:
        raise NotImplementedError

    async def close(self) -> None:
        return None
