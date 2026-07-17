from __future__ import annotations

from app.core.config import Settings
from app.services.tts.base import TTSProvider
from app.services.tts.elevenlabs import ElevenLabsTTSProvider
from app.services.tts.google_tts import GoogleTTSProvider
from app.services.tts.mock import MockTTSProvider


def build_tts_provider(settings: Settings) -> TTSProvider:
    if settings.tts_provider == "google":
        return GoogleTTSProvider(settings.google_api_key)
    if settings.tts_provider == "elevenlabs":
        return ElevenLabsTTSProvider(
            settings.elevenlabs_api_key,
            voice_id=settings.elevenlabs_voice_id,
            model_id=settings.elevenlabs_model_id,
        )
    return MockTTSProvider()
