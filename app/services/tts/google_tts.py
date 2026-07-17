from __future__ import annotations

import time

import httpx

from app.core.exceptions import ProviderError
from app.core.logging import get_logger
from app.services.tts.base import SynthesisResult, TTSProvider

logger = get_logger(__name__)


class GoogleTTSProvider(TTSProvider):
    """Google Cloud Text-to-Speech REST API."""

    name = "google"

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ProviderError("GOOGLE_API_KEY is required for Google TTS")
        self._api_key = api_key
        self._client = httpx.AsyncClient(timeout=30.0)

    async def synthesize(self, text: str, *, locale: str = "en-US") -> SynthesisResult:
        started = time.perf_counter()
        voice_name = "en-US-Neural2-C" if locale.startswith("en") else None
        body = {
            "input": {"text": text},
            "voice": {"languageCode": locale, **({"name": voice_name} if voice_name else {})},
            "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": 16000},
        }
        try:
            response = await self._client.post(
                f"https://texttospeech.googleapis.com/v1/text:synthesize?key={self._api_key}",
                json=body,
            )
            response.raise_for_status()
            import base64

            audio_b64 = response.json()["audioContent"]
            audio = base64.b64decode(audio_b64)
        except Exception as exc:  # noqa: BLE001
            logger.exception("google_tts_failed")
            raise ProviderError(f"Google TTS failed: {exc}") from exc

        return SynthesisResult(
            audio=audio,
            content_type="audio/l16",
            latency_ms=int((time.perf_counter() - started) * 1000),
            transcript=text,
        )

    async def close(self) -> None:
        await self._client.aclose()
