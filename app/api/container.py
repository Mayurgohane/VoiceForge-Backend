from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings
from app.infrastructure.database import Database
from app.infrastructure.redis_client import RedisClient
from app.services.agent.runtime import VoiceAgent
from app.services.conversation_pipeline import ConversationPipeline
from app.services.handoff import HandoffPolicy
from app.services.redaction import PIIRedactor
from app.services.session_manager import SessionManager
from app.services.stt import build_stt_provider
from app.services.stt.base import STTProvider
from app.services.telephony.warm_transfer import WarmTransferService
from app.services.tools import build_tool_registry
from app.services.tools.authz import SecuredToolRegistry
from app.services.tts import build_tts_provider
from app.services.tts.base import TTSProvider


@dataclass
class AppContainer:
    settings: Settings
    db: Database
    redis: RedisClient
    sessions: SessionManager
    stt: STTProvider
    tts: TTSProvider
    agent: VoiceAgent
    pipeline: ConversationPipeline
    warm_transfer: WarmTransferService

    async def startup(self) -> None:
        await self.db.connect()
        await self.redis.connect()

    async def shutdown(self) -> None:
        await self.stt.close()
        await self.tts.close()
        await self.redis.disconnect()
        await self.db.disconnect()


def build_container(settings: Settings) -> AppContainer:
    db = Database(settings)
    redis = RedisClient(settings)
    sessions = SessionManager(settings, redis, db)
    stt = build_stt_provider(settings)
    tts = build_tts_provider(settings)
    plain_tools = build_tool_registry()
    secured_tools = SecuredToolRegistry(
        tools=plain_tools.as_dict(),
        redis=redis,
        settings=settings,
    )
    agent = VoiceAgent(settings, secured_tools)
    warm_transfer = WarmTransferService(settings, redis)
    pipeline = ConversationPipeline(
        settings=settings,
        sessions=sessions,
        stt=stt,
        tts=tts,
        agent=agent,
        redactor=PIIRedactor(),
        handoff=HandoffPolicy(settings),
        warm_transfer=warm_transfer,
    )
    return AppContainer(
        settings=settings,
        db=db,
        redis=redis,
        sessions=sessions,
        stt=stt,
        tts=tts,
        agent=agent,
        pipeline=pipeline,
        warm_transfer=warm_transfer,
    )
