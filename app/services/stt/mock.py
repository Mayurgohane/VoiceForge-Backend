from __future__ import annotations

import hashlib
import time

from app.services.stt.base import STTProvider, TranscriptResult


class MockSTTProvider(STTProvider):
    """Deterministic STT for local development and tests."""

    name = "mock"

    def __init__(self) -> None:
        self._buffers: dict[str, bytearray] = {}

    def _buf(self, session_id: str | None) -> bytearray:
        key = session_id or "_default"
        if key not in self._buffers:
            self._buffers[key] = bytearray()
        return self._buffers[key]

    async def transcribe_chunk(
        self,
        audio: bytes,
        *,
        sequence: int,
        is_final: bool = False,
        locale: str = "en-US",  # noqa: ARG002
        session_id: str | None = None,
    ) -> TranscriptResult | None:
        started = time.perf_counter()
        buf = self._buf(session_id)
        buf.extend(audio)

        try:
            text = audio.decode("utf-8").strip()
            if text and all(ch.isprintable() or ch.isspace() for ch in text):
                buf.clear()
                return TranscriptResult(
                    text=text,
                    is_final=True,
                    confidence=0.99,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    speech_final=True,
                )
        except UnicodeDecodeError:
            pass

        if not is_final and sequence % 3 != 0:
            return TranscriptResult(
                text="...",
                is_final=False,
                confidence=0.4,
                latency_ms=int((time.perf_counter() - started) * 1000),
            )

        digest = hashlib.sha1(bytes(buf[-64:])).hexdigest()[:6]
        phrase = f"mock utterance {digest}"
        buf.clear()
        return TranscriptResult(
            text=phrase,
            is_final=True,
            confidence=0.85,
            latency_ms=int((time.perf_counter() - started) * 1000),
            speech_final=True,
        )
