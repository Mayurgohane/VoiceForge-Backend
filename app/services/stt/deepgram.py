from __future__ import annotations

import base64
import time

import httpx

from app.core.exceptions import ProviderError
from app.core.logging import get_logger
from app.services.stt.base import STTProvider, TranscriptResult
from app.services.stt.deepgram_live import DeepgramLiveConfig, DeepgramLiveSession

logger = get_logger(__name__)


class DeepgramSTTProvider(STTProvider):
    """Deepgram STT with per-session REST buffers + live session factory."""

    name = "deepgram"

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "nova-2",
        endpointing_ms: int = 300,
    ) -> None:
        if not api_key:
            raise ProviderError("DEEPGRAM_API_KEY is required for deepgram STT")
        self._api_key = api_key
        self._model = model
        self._endpointing_ms = endpointing_ms
        self._client = httpx.AsyncClient(
            base_url="https://api.deepgram.com/v1",
            headers={"Authorization": f"Token {api_key}"},
            timeout=30.0,
        )
        self._buffers: dict[str, bytearray] = {}

    def _buf(self, session_id: str | None) -> bytearray:
        key = session_id or "_default"
        if key not in self._buffers:
            self._buffers[key] = bytearray()
        return self._buffers[key]

    def create_live_session(
        self,
        *,
        language: str = "en",
        encoding: str = "mulaw",
        sample_rate: int = 8000,
    ) -> DeepgramLiveSession:
        return DeepgramLiveSession(
            DeepgramLiveConfig(
                api_key=self._api_key,
                model=self._model,
                language=language,
                encoding=encoding,
                sample_rate=sample_rate,
                endpointing_ms=self._endpointing_ms,
            )
        )

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
        language = locale.split("-")[0]

        content_type = "audio/wav"
        params: dict[str, str] = {
            "model": self._model,
            "smart_format": "true",
            "language": language,
            "punctuate": "true",
        }
        if payload and not payload.startswith(b"RIFF"):
            params.update({"encoding": "mulaw", "sample_rate": "8000", "channels": "1"})
            content_type = "application/octet-stream"

        try:
            response = await self._client.post(
                "/listen",
                params=params,
                content=payload,
                headers={"Content-Type": content_type},
            )
            response.raise_for_status()
            data = response.json()
            alt = data["results"]["channels"][0]["alternatives"][0]
            text = alt.get("transcript", "").strip()
            confidence = float(alt.get("confidence", 0.0))
        except Exception as exc:  # noqa: BLE001
            logger.exception("deepgram_stt_failed", session_id=session_id)
            raise ProviderError(f"Deepgram STT failed: {exc}") from exc

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


def encode_pcm_as_data_url(pcm: bytes) -> str:
    return base64.b64encode(pcm).decode()
