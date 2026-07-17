from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.models import VoiceSession
from app.infrastructure.redis_client import RedisClient

# Twilio warm-transfer: AI handoff → park caller in conference → whisper agent → join.

logger = get_logger(__name__)


@dataclass(slots=True)
class WarmTransferResult:
    success: bool
    conference_name: str
    agent_call_sid: str | None = None
    error: str | None = None


class WarmTransferService:
    def __init__(self, settings: Settings, redis: RedisClient) -> None:
        self._settings = settings
        self._redis = redis

    @property
    def enabled(self) -> bool:
        return bool(
            self._settings.warm_transfer_enabled
            and self._settings.twilio_account_sid
            and self._settings.twilio_auth_token
            and self._settings.twilio_agent_number
            and self._settings.twilio_phone_number
        )

    def conference_name(self, session_id: str) -> str:
        return f"voiceforge-{session_id}"

    async def store_transfer_context(
        self,
        session_id: str,
        *,
        summary: str,
        reason: str,
        caller_id: str | None,
        call_sid: str | None,
    ) -> None:
        await self._redis.set_json(
            f"voiceforge:transfer:{session_id}",
            {
                "summary": summary[:500],
                "reason": reason,
                "caller_id": caller_id,
                "call_sid": call_sid,
                "conference": self.conference_name(session_id),
            },
            ttl=self._settings.session_ttl_seconds,
        )

    async def get_transfer_context(self, session_id: str) -> dict[str, Any] | None:
        return await self._redis.get_json(f"voiceforge:transfer:{session_id}")

    async def start(self, session: VoiceSession, *, reason: str, summary: str) -> WarmTransferResult:
        if not self.enabled:
            return WarmTransferResult(
                success=False,
                conference_name=self.conference_name(session.id),
                error="Warm transfer is not configured",
            )

        call_sid = (
            (session.metadata or {}).get("call_sid")
            or (session.metadata or {}).get("twilio_call_sid")
        )
        if not call_sid:
            return WarmTransferResult(
                success=False,
                conference_name=self.conference_name(session.id),
                error="Missing Twilio CallSid on session",
            )

        conference = self.conference_name(session.id)
        await self.store_transfer_context(
            session.id,
            summary=summary,
            reason=reason,
            caller_id=session.caller_id,
            call_sid=str(call_sid),
        )

        join_url = (
            f"{self._settings.public_base_url.rstrip('/')}"
            f"{self._settings.api_prefix}/twilio/transfer/{session.id}/caller"
        )
        agent_url = (
            f"{self._settings.public_base_url.rstrip('/')}"
            f"{self._settings.api_prefix}/twilio/transfer/{session.id}/agent"
        )

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                redirect = await client.post(
                    f"https://api.twilio.com/2010-04-01/Accounts/"
                    f"{self._settings.twilio_account_sid}/Calls/{call_sid}.json",
                    auth=(self._settings.twilio_account_sid, self._settings.twilio_auth_token),
                    data={"Url": join_url, "Method": "POST"},
                )
                redirect.raise_for_status()

                agent_call = await client.post(
                    f"https://api.twilio.com/2010-04-01/Accounts/"
                    f"{self._settings.twilio_account_sid}/Calls.json",
                    auth=(self._settings.twilio_account_sid, self._settings.twilio_auth_token),
                    data={
                        "To": self._settings.twilio_agent_number,
                        "From": self._settings.twilio_phone_number,
                        "Url": agent_url,
                        "Method": "POST",
                        "Timeout": str(self._settings.warm_transfer_agent_timeout_seconds),
                    },
                )
                agent_call.raise_for_status()
                agent_sid = agent_call.json().get("sid")
        except Exception as exc:  # noqa: BLE001
            logger.exception("warm_transfer_failed", session_id=session.id, error=str(exc))
            return WarmTransferResult(
                success=False,
                conference_name=conference,
                error=str(exc),
            )

        logger.info(
            "warm_transfer_started",
            session_id=session.id,
            call_sid=call_sid,
            agent_call_sid=agent_sid,
            conference=conference,
        )
        return WarmTransferResult(
            success=True,
            conference_name=conference,
            agent_call_sid=agent_sid,
        )


def caller_conference_twiml(session_id: str, *, wait_url: str | None = None) -> str:
    conference = html.escape(f"voiceforge-{session_id}", quote=True)
    wait_attr = f' waitUrl="{html.escape(wait_url, quote=True)}"' if wait_url else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="Polly.Joanna">Please hold while I connect you to a specialist.</Say>
  <Dial>
    <Conference startConferenceOnEnter="true" endConferenceOnExit="true"{wait_attr}>{conference}</Conference>
  </Dial>
</Response>"""


def agent_whisper_twiml(session_id: str, *, summary: str, reason: str) -> str:
    safe_summary = html.escape(summary[:400], quote=True)
    safe_reason = html.escape(reason, quote=True)
    conference = html.escape(f"voiceforge-{session_id}", quote=True)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="Polly.Joanna">Warm transfer. Reason: {safe_reason}. Customer context: {safe_summary}</Say>
  <Pause length="1"/>
  <Say voice="Polly.Joanna">Connecting you to the caller now.</Say>
  <Dial>
    <Conference startConferenceOnEnter="true" endConferenceOnExit="false" beep="false">{conference}</Conference>
  </Dial>
</Response>"""


def build_signed_public_path(settings: Settings, path: str, params: dict[str, str] | None = None) -> str:
    base = settings.public_base_url.rstrip("/")
    query = f"?{urlencode(params)}" if params else ""
    return f"{base}{path}{query}"
