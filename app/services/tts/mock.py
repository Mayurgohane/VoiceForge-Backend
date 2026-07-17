from __future__ import annotations

import time
import wave
from io import BytesIO

from app.services.tts.base import SynthesisResult, TTSProvider


class MockTTSProvider(TTSProvider):
    """Generates a short silent WAV so the streaming path is exercisable offline."""

    name = "mock"

    async def synthesize(self, text: str, *, locale: str = "en-US") -> SynthesisResult:  # noqa: ARG002
        started = time.perf_counter()
        duration_sec = min(2.0, max(0.4, len(text) / 40))
        sample_rate = 16000
        n_frames = int(sample_rate * duration_sec)
        buf = BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(b"\x00\x00" * n_frames)
        return SynthesisResult(
            audio=buf.getvalue(),
            content_type="audio/wav",
            latency_ms=int((time.perf_counter() - started) * 1000),
            transcript=text,
        )
