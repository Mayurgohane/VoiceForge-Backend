from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DEBUG", "false")
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


def test_health(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "degraded"}
    assert body["app"] == "VoiceForge"


def test_create_session_and_text_turn(client: TestClient) -> None:
    headers = {"X-API-Key": "test-api-key-123"}
    created = client.post(
        "/api/v1/sessions",
        json={"channel": "simulation", "caller_id": "demo-user"},
        headers=headers,
    )
    assert created.status_code == 201
    session_id = created.json()["id"]

    turn = client.post(
        f"/api/v1/sessions/{session_id}/turns/text",
        json={"text": "Where is my order ORD-10482?"},
        headers=headers,
    )
    assert turn.status_code == 200
    events = turn.json()["events"]
    types = [e["type"] for e in events]
    assert "agent.response" in types
    assert "tts.audio" in types
    assert "metrics" in types

    fetched = client.get(f"/api/v1/sessions/{session_id}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["turn_count"] >= 2


def test_handoff_on_human_request(client: TestClient) -> None:
    headers = {"X-API-Key": "test-api-key-123"}
    created = client.post("/api/v1/sessions", json={}, headers=headers)
    session_id = created.json()["id"]

    turn = client.post(
        f"/api/v1/sessions/{session_id}/turns/text",
        json={"text": "I want to speak to a human please"},
        headers=headers,
    )
    assert turn.status_code == 200
    types = [e["type"] for e in turn.json()["events"]]
    assert "handoff" in types

    session = client.get(f"/api/v1/sessions/{session_id}", headers=headers)
    assert session.json()["status"] == "waiting_human"


def test_empty_text_turn_rejected(client: TestClient) -> None:
    headers = {"X-API-Key": "test-api-key-123"}
    created = client.post("/api/v1/sessions", json={}, headers=headers)
    session_id = created.json()["id"]
    turn = client.post(
        f"/api/v1/sessions/{session_id}/turns/text",
        json={"text": "   "},
        headers=headers,
    )
    assert turn.status_code == 422


def test_turn_after_handoff_rejected(client: TestClient) -> None:
    headers = {"X-API-Key": "test-api-key-123"}
    created = client.post("/api/v1/sessions", json={}, headers=headers)
    session_id = created.json()["id"]
    client.post(
        f"/api/v1/sessions/{session_id}/turns/text",
        json={"text": "I want to speak to a human please"},
        headers=headers,
    )
    second = client.post(
        f"/api/v1/sessions/{session_id}/turns/text",
        json={"text": "hello again"},
        headers=headers,
    )
    assert second.status_code == 409


def test_unauthorized_without_api_key(client: TestClient) -> None:
    response = client.post(
        "/api/v1/sessions",
        json={},
        headers={"X-API-Key": "wrong-key"},
    )
    assert response.status_code == 401
