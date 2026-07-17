from __future__ import annotations

import time

import httpx

from app.core.exceptions import ProviderError
from app.core.logging import get_logger
from app.services.tts.base import SynthesisResult, TTSProvider

logger = get_logger(__name__)


class ElevenLabsTTSProvider(TTSProvider):
    """ElevenLabs Text-to-Speech REST API (MP3 output)."""

    name = "elevenlabs"

    def __init__(
        self,
        api_key: str,
        *,
        voice_id: str = "21m00Tcm4TlvDq8ikWAM",
        model_id: str = "eleven_monolingual_v1",
    ) -> None:
        if not api_key:
            raise ProviderError("ELEVENLABS_API_KEY is required for ElevenLabs TTS")
        self._api_key = api_key
        self._voice_id = voice_id
        self._model_id = model_id
        self._client = httpx.AsyncClient(
            base_url="https://api.elevenlabs.io/v1",
            headers={
                "xi-api-key": api_key,
                "Accept": "audio/mpeg",
                "Content-Type": "application/json",
            },
            timeout=45.0,
        )

    async def synthesize(self, text: str, *, locale: str = "en-US") -> SynthesisResult:  # noqa: ARG002
        started = time.perf_counter()
        body = {
            "text": text,
            "model_id": self._model_id,
            "voice_settings": {"stability": 0.4, "similarity_boost": 0.75},
        }
        try:
            response = await self._client.post(
                f"/text-to-speech/{self._voice_id}",
                json=body,
            )
            response.raise_for_status()
            audio = response.content
        except Exception as exc:  # noqa: BLE001
            logger.exception("elevenlabs_tts_failed")
            raise ProviderError(f"ElevenLabs TTS failed: {exc}") from exc

        return SynthesisResult(
            audio=audio,
            content_type="audio/mpeg",
            latency_ms=int((time.perf_counter() - started) * 1000),
            transcript=text,
        )

    async def close(self) -> None:
        await self._client.aclose()
