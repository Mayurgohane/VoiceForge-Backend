from __future__ import annotations

import base64

import pytest

from app.core.config import Settings
from app.core.exceptions import UnauthorizedError
from app.core.security import verify_api_key
from app.infrastructure.redis_client import InMemoryKV, RedisClient
from app.services.redaction import PIIRedactor
from app.services.stt.deepgram_live import DeepgramLiveConfig, DeepgramLiveSession
from app.services.telephony.audio import (
    audio_to_twilio_payload,
    mulaw_decode,
    mulaw_encode,
    pcm16_to_twilio_mulaw,
    split_mulaw_for_twilio,
)
from app.services.telephony.twilio_security import (
    create_stream_token,
    twilio_signature_candidates,
    validate_twilio_signature,
    verify_stream_token,
)
from app.services.telephony.warm_transfer import agent_whisper_twiml, caller_conference_twiml
from app.services.tools.authz import SecuredToolRegistry, ToolContext
from app.services.tools.crm import CRMLookupTool
from app.services.tools.knowledge import KnowledgeSearchTool
from app.services.tools.tickets import TicketTool


@pytest.fixture
async def memory_redis() -> RedisClient:
    settings = Settings(app_env="development", api_key="test-api-key-123456")
    client = RedisClient(settings)
    client._redis = InMemoryKV()
    client.using_memory = True
    return client


def test_mulaw_roundtrip() -> None:
    pcm = b"\x00\x10" * 160
    mulaw = mulaw_encode(pcm)
    restored = mulaw_decode(mulaw)
    assert len(mulaw) == 160
    assert len(restored) == len(pcm)


def test_odd_length_pcm_does_not_crash() -> None:
    odd = b"\x00\x01\x02"
    mulaw = pcm16_to_twilio_mulaw(odd, src_rate=16000)
    assert isinstance(mulaw, (bytes, bytearray))


def test_pcm_to_twilio_and_chunking() -> None:
    pcm16k = b"\x00\x00" * 640
    mulaw = pcm16_to_twilio_mulaw(pcm16k, src_rate=16000)
    frames = split_mulaw_for_twilio(mulaw, frame_ms=20)
    assert frames
    assert all(len(f) <= 160 for f in frames)


def test_wav_payload_conversion() -> None:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 320)
    mulaw = audio_to_twilio_payload(buf.getvalue(), "audio/wav")
    assert len(mulaw) > 0


@pytest.mark.asyncio
async def test_stream_token_allows_reconnect_same_token(memory_redis: RedisClient) -> None:
    settings = Settings(api_key="test-api-key-123456")
    token = await create_stream_token(settings, memory_redis, "session-1", ttl_seconds=60)
    await verify_stream_token(settings, memory_redis, "session-1", token)
    # Twilio media reconnect / network blip with the same token must succeed.
    await verify_stream_token(settings, memory_redis, "session-1", token)


@pytest.mark.asyncio
async def test_stream_token_rejects_different_token_after_bind(memory_redis: RedisClient) -> None:
    settings = Settings(api_key="test-api-key-123456")
    token_a = await create_stream_token(settings, memory_redis, "session-1", ttl_seconds=60)
    token_b = await create_stream_token(settings, memory_redis, "session-1", ttl_seconds=60)
    await verify_stream_token(settings, memory_redis, "session-1", token_a)
    with pytest.raises(UnauthorizedError):
        await verify_stream_token(settings, memory_redis, "session-1", token_b)


@pytest.mark.asyncio
async def test_stream_token_rejects_tamper(memory_redis: RedisClient) -> None:
    settings = Settings(api_key="test-api-key-123456")
    token = await create_stream_token(settings, memory_redis, "session-1", ttl_seconds=60)
    bad = token[:-4] + "dead"
    with pytest.raises(UnauthorizedError):
        await verify_stream_token(settings, memory_redis, "session-1", bad)


def test_twilio_signature_validation() -> None:
    import hashlib
    import hmac

    auth = "secret_token"
    url = "https://example.com/api/v1/twilio/voice"
    params = {"CallSid": "CA123", "From": "+15551234567"}
    s = url + "CallSid" + "CA123" + "From" + "+15551234567"
    digest = hmac.new(auth.encode(), s.encode(), hashlib.sha1).digest()
    signature = base64.b64encode(digest).decode()
    assert validate_twilio_signature(
        auth_token=auth,
        url=url,
        params=params,
        signature=signature,
    )


def test_twilio_signature_candidates_include_https_http() -> None:
    urls = twilio_signature_candidates(
        request_url="http://127.0.0.1:8000/api/v1/twilio/voice",
        public_base_url="https://abc.ngrok-free.app",
        path="/api/v1/twilio/voice",
        query="",
    )
    assert "https://abc.ngrok-free.app/api/v1/twilio/voice" in urls
    assert "http://abc.ngrok-free.app/api/v1/twilio/voice" in urls


def test_verify_api_key_different_lengths() -> None:
    assert verify_api_key("short", "a-much-longer-api-key-value") is False
    assert verify_api_key("same-key-value", "same-key-value") is True


def test_pii_audit_labels_not_raw_values() -> None:
    redactor = PIIRedactor()
    result = redactor.redact("email me at mayur@example.com")
    labels = sorted({m.split(":", 1)[0] for m in result.matches})
    assert "email" in labels
    assert "mayur@example.com" not in ",".join(labels)


def test_deepgram_speech_final_policy() -> None:
    session = DeepgramLiveSession(DeepgramLiveConfig(api_key="test-key", model="nova-2"))
    interim = session._parse_message(
        {
            "type": "Results",
            "is_final": False,
            "speech_final": False,
            "channel": {"alternatives": [{"transcript": "where is", "confidence": 0.5}]},
        }
    )
    assert interim is not None and interim.is_final is False

    seg = session._parse_message(
        {
            "type": "Results",
            "is_final": True,
            "speech_final": False,
            "channel": {"alternatives": [{"transcript": "where is my", "confidence": 0.8}]},
        }
    )
    assert seg is not None and seg.is_final is False

    final = session._parse_message(
        {
            "type": "Results",
            "is_final": True,
            "speech_final": True,
            "channel": {"alternatives": [{"transcript": "order", "confidence": 0.9}]},
        }
    )
    assert final is not None and final.is_final is True and "order" in final.text


def test_warm_transfer_twiml_contains_conference() -> None:
    caller = caller_conference_twiml("abc-123")
    agent = agent_whisper_twiml("abc-123", summary="Needs order help", reason="user_requested")
    assert "voiceforge-abc-123" in caller
    assert "Conference" in caller
    assert "Warm transfer" in agent
    assert "Needs order help" in agent


@pytest.mark.asyncio
async def test_tool_authz_blocks_foreign_phone(memory_redis: RedisClient) -> None:
    settings = Settings(
        api_key="test-api-key-123456",
        tool_authz_enabled=True,
        tool_strict_caller_bind=True,
        tool_rate_limit_per_minute=20,
    )
    registry = SecuredToolRegistry(
        tools={
            "crm_lookup": CRMLookupTool(),
            "create_ticket": TicketTool(),
            "knowledge_search": KnowledgeSearchTool(),
        },
        redis=memory_redis,
        settings=settings,
    )
    ctx = ToolContext(session_id="s1", caller_id="+15550001111", channel="twilio")
    denied = await registry.execute("crm_lookup", ctx, identifier="+19998887777")
    assert denied.success is False
    assert "does not match" in (denied.error or "")

    allowed = await registry.execute("crm_lookup", ctx, identifier="ORD-10482")
    assert allowed.success is True


@pytest.mark.asyncio
async def test_tool_rate_limit(memory_redis: RedisClient) -> None:
    settings = Settings(
        api_key="test-api-key-123456",
        tool_authz_enabled=True,
        tool_strict_caller_bind=False,
        tool_rate_limit_per_minute=2,
    )
    registry = SecuredToolRegistry(
        tools={"knowledge_search": KnowledgeSearchTool()},
        redis=memory_redis,
        settings=settings,
    )
    ctx = ToolContext(session_id="s-rate", caller_id="+1", channel="ws")
    assert (await registry.execute("knowledge_search", ctx, query="order")).success
    assert (await registry.execute("knowledge_search", ctx, query="order")).success
    limited = await registry.execute("knowledge_search", ctx, query="order")
    assert limited.success is False
    assert "Rate limit" in (limited.error or "")
