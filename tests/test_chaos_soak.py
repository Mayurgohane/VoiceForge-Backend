"""Load / chaos / soak tests (in-process, mock providers).

Run:
  pytest -q tests/test_chaos_soak.py
  pytest -q -m soak
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.exceptions import ProviderError
from app.main import create_app
from app.services.stt.google_stt import GoogleSTTProvider
from app.services.tts.elevenlabs import ElevenLabsTTSProvider


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "soak.db"
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("API_KEY", "test-api-key-123")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/15")
    monkeypatch.setenv("STT_PROVIDER", "mock")
    monkeypatch.setenv("TTS_PROVIDER", "mock")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("AUTO_MIGRATE", "true")
    monkeypatch.setenv("PROMETHEUS_ENABLED", "false")
    get_settings.cache_clear()
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.soak
def test_concurrent_text_turns_load(client: TestClient) -> None:
    headers = {"X-API-Key": "test-api-key-123"}

    def run_session(i: int) -> None:
        created = client.post(
            "/api/v1/sessions",
            json={"channel": "simulation", "caller_id": f"user-{i}"},
            headers=headers,
        )
        assert created.status_code == 201
        session_id = created.json()["id"]
        turn = client.post(
            f"/api/v1/sessions/{session_id}/turns/text",
            json={"text": f"Where is my order ORD-{10000 + i}?"},
            headers=headers,
        )
        assert turn.status_code == 200
        types = [e["type"] for e in turn.json()["events"]]
        assert "agent.response" in types or "handoff" in types

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(run_session, range(12)))


@pytest.mark.soak
def test_chaos_handoff_and_continue(client: TestClient) -> None:
    headers = {"X-API-Key": "test-api-key-123"}
    created = client.post("/api/v1/sessions", json={}, headers=headers)
    session_id = created.json()["id"]

    ok = client.post(
        f"/api/v1/sessions/{session_id}/turns/text",
        json={"text": "Where is my order ORD-10482?"},
        headers=headers,
    )
    assert ok.status_code == 200

    handoff = client.post(
        f"/api/v1/sessions/{session_id}/turns/text",
        json={"text": "please get me a real person"},
        headers=headers,
    )
    assert handoff.status_code == 200
    assert any(e["type"] == "handoff" for e in handoff.json()["events"])

    again = client.post(
        f"/api/v1/sessions/{session_id}/turns/text",
        json={"text": "hello again"},
        headers=headers,
    )
    assert again.status_code == 409


@pytest.mark.soak
def test_health_reports_schema_revision(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    checks = response.json()["checks"]
    assert checks["database_revision"] in {"001_initial", "unknown"}
    assert "database_pool" in checks
    assert "redis_stats" in checks


def test_google_stt_requires_key() -> None:
    with pytest.raises(ProviderError):
        GoogleSTTProvider("")


def test_elevenlabs_requires_key() -> None:
    with pytest.raises(ProviderError):
        ElevenLabsTTSProvider("")


@pytest.mark.asyncio
async def test_provider_factory_wiring() -> None:
    from app.services.stt import build_stt_provider
    from app.services.tts import build_tts_provider

    stt = build_stt_provider(Settings(stt_provider="google", google_api_key="fake-google-key"))
    tts = build_tts_provider(
        Settings(
            tts_provider="elevenlabs",
            elevenlabs_api_key="fake-eleven-key",
        )
    )
    assert stt.name == "google"
    assert tts.name == "elevenlabs"
    await stt.close()
    await tts.close()
