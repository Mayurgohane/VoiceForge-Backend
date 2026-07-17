from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "VoiceForge"
    app_env: Literal["development", "staging", "production"] = "development"
    debug: bool = False
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"
    host: str = "0.0.0.0"
    port: int = 8000

    api_key: str = Field(default="change-me-in-production", min_length=8)
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://localhost:5173"]
    )

    database_url: str = "sqlite+aiosqlite:///./voiceforge.db"
    redis_url: str = "redis://localhost:6379/0"
    session_ttl_seconds: int = 3600
    auto_migrate: bool = True
    db_create_all: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_recycle_seconds: int = 1800
    db_pool_timeout_seconds: int = 30
    redis_max_connections: int = 50
    redis_socket_timeout_seconds: float = 5.0
    redis_health_retries: int = 2

    stt_provider: Literal["mock", "deepgram", "google"] = "mock"
    tts_provider: Literal["mock", "google", "elevenlabs"] = "mock"
    llm_provider: Literal["mock", "gemini"] = "mock"

    google_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    deepgram_api_key: str = ""
    deepgram_model: str = "nova-2"
    deepgram_endpointing_ms: int = 300
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"
    elevenlabs_model_id: str = "eleven_monolingual_v1"

    max_turn_latency_ms: int = 1500
    barge_in_enabled: bool = True
    handoff_confidence_threshold: float = 0.35
    enable_pii_redaction: bool = True
    max_user_text_chars: int = 4000
    silence_timeout_seconds: int = 45
    auth_bypass: bool = False
    require_metrics_auth: bool = True

    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str = "http://localhost:4318"
    prometheus_enabled: bool = True

    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""
    twilio_agent_number: str = ""
    twilio_greeting: str = "Hi, you are connected to VoiceForge. How can I help you today?"
    public_base_url: str = "http://localhost:8000"
    stream_token_ttl_seconds: int = 900
    warm_transfer_enabled: bool = False
    warm_transfer_agent_timeout_seconds: int = 30

    tool_authz_enabled: bool = True
    tool_strict_caller_bind: bool = True
    tool_rate_limit_per_minute: int = 20

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors(cls, value: object) -> object:
        if isinstance(value, str):
            raw = value.strip()
            if raw.startswith("["):
                import json

                return json.loads(raw)
            return [part.strip() for part in raw.split(",") if part.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def redis_optional(self) -> bool:
        """Allow in-memory session store when Redis is unavailable in development."""
        return self.app_env == "development"

    @property
    def is_staging(self) -> bool:
        return self.app_env == "staging"


@lru_cache
def get_settings() -> Settings:
    return Settings()
