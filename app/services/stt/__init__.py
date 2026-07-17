from __future__ import annotations

from app.core.config import Settings
from app.services.stt.base import STTProvider
from app.services.stt.deepgram import DeepgramSTTProvider
from app.services.stt.google_stt import GoogleSTTProvider
from app.services.stt.mock import MockSTTProvider


def build_stt_provider(settings: Settings) -> STTProvider:
    if settings.stt_provider == "deepgram":
        return DeepgramSTTProvider(
            settings.deepgram_api_key,
            model=settings.deepgram_model,
            endpointing_ms=settings.deepgram_endpointing_ms,
        )
    if settings.stt_provider == "google":
        return GoogleSTTProvider(settings.google_api_key)
    return MockSTTProvider()
