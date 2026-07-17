from __future__ import annotations

import base64
import time
import wave
from io import BytesIO

import httpx

from app.core.exceptions import ProviderError
from app.core.logging import get_logger
from app.services.stt.base import STTProvider, TranscriptResult

logger = get_logger(__name__)


class GoogleSTTProvider(STTProvider):
    """Google Cloud Speech-to-Text REST (API key) for chunked browser/sim audio."""

    name = "google"

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ProviderError("GOOGLE_API_KEY is required for Google STT")
        self._api_key = api_key
        self._client = httpx.AsyncClient(timeout=30.0)
        self._buffers: dict[str, bytearray] = {}

    def _buf(self, session_id: str | None) -> bytearray:
        key = session_id or "_default"
        if key not in self._buffers:
            self._buffers[key] = bytearray()
        return self._buffers[key]

    @staticmethod
    def _pcm16_from_payload(payload: bytes) -> tuple[bytes, int]:
        """Return (pcm16le, sample_rate). Accepts WAV or raw LINEAR16 @ 16 kHz."""
        if payload.startswith(b"RIFF"):
            with wave.open(BytesIO(payload), "rb") as wf:
                if wf.getsampwidth() != 2 or wf.getnchannels() != 1:
                    raise ProviderError("Google STT expects mono 16-bit WAV")
                return wf.readframes(wf.getnframes()), int(wf.getframerate())
        return payload, 16000

    async def transcribe_chunk(
        self,
        audio: bytes,
        *,
        sequence: int,  # noqa: ARG002
        is_final: bool = False,
        locale: str = "en-US",
        session_id: str | None = None,
    ) -> TranscriptResult | None:
        started = time.perf_counter()
        buf = self._buf(session_id)
        buf.extend(audio)
        if not is_final and len(buf) < 16000:
            return None

        payload = bytes(buf)
        buf.clear()
        if not payload:
            return None

        try:
            pcm, sample_rate = self._pcm16_from_payload(payload)
            body = {
                "config": {
                    "encoding": "LINEAR16",
                    "sampleRateHertz": sample_rate,
                    "languageCode": locale,
                    "enableAutomaticPunctuation": True,
                },
                "audio": {"content": base64.b64encode(pcm).decode("ascii")},
            }
            response = await self._client.post(
                f"https://speech.googleapis.com/v1/speech:recognize?key={self._api_key}",
                json=body,
            )
            response.raise_for_status()
            data = response.json()
            results = data.get("results") or []
            if not results:
                return None
            alt = results[0]["alternatives"][0]
            text = str(alt.get("transcript") or "").strip()
            confidence = float(alt.get("confidence", 0.9))
        except Exception as exc:  # noqa: BLE001
            logger.exception("google_stt_failed", session_id=session_id)
            raise ProviderError(f"Google STT failed: {exc}") from exc

        if not text:
            return None

        return TranscriptResult(
            text=text,
            is_final=True,
            confidence=confidence,
            latency_ms=int((time.perf_counter() - started) * 1000),
            speech_final=True,
        )

    async def close(self) -> None:
        await self._client.aclose()
