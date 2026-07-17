from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.models import TranscriptTurn
from app.services.agent.state import SYSTEM_PROMPT, AgentState, history_from_turns
from app.services.tools.authz import SecuredToolRegistry, ToolContext

logger = get_logger(__name__)


@dataclass(slots=True)
class AgentResponse:
    text: str
    confidence: float
    tool_results: list[dict[str, Any]]
    latency_ms: int
    should_handoff: bool = False
    handoff_reason: str | None = None
    raw_state: dict[str, Any] | None = None


class VoiceAgent:
    """Tool-calling voice agent with mock + Gemini backends."""

    def __init__(self, settings: Settings, tools: SecuredToolRegistry) -> None:
        self._settings = settings
        self._tools = tools

    async def run(
        self,
        *,
        session_id: str,
        user_text: str,
        history: list[TranscriptTurn],
        locale: str = "en-US",
        caller_id: str | None = None,
        channel: str = "unknown",
    ) -> AgentResponse:
        started = time.perf_counter()
        ctx = ToolContext(session_id=session_id, caller_id=caller_id, channel=channel)
        state: AgentState = {
            "session_id": session_id,
            "user_text": user_text,
            "locale": locale,
            "history": history_from_turns(history),
            "tool_calls": [],
            "tool_results": [],
            "confidence": 0.8,
            "should_handoff": False,
            "handoff_reason": None,
        }

        if self._settings.llm_provider == "gemini" and self._settings.google_api_key:
            response = await self._run_gemini(state, ctx)
        else:
            response = await self._run_mock(state, ctx)

        response.latency_ms = int((time.perf_counter() - started) * 1000)
        return response

    async def _run_mock(self, state: AgentState, ctx: ToolContext) -> AgentResponse:
        text = state["user_text"].strip()
        lowered = text.lower()
        tool_results: list[dict[str, Any]] = []

        if any(p in lowered for p in ("human", "agent please", "real person")):
            return AgentResponse(
                text="Understood. Connecting you to a human specialist now.",
                confidence=0.95,
                tool_results=[],
                latency_ms=0,
                should_handoff=True,
                handoff_reason="user_requested",
            )

        if any(k in lowered for k in ("order", "shipment", "tracking", "crm", "account")):
            identifier = self._extract_identifier(text)
            kwargs: dict[str, Any] = {}
            if identifier:
                kwargs["identifier"] = identifier
            result = await self._tools.execute("crm_lookup", ctx, **kwargs)
            tool_results.append(result.to_dict())
            if result.success:
                data = result.data
                reply = (
                    f"I found your account, {data.get('name')}. "
                    f"Your latest order {data.get('last_order_id')} is {data.get('last_order_status')}."
                )
                confidence = 0.9
            else:
                reply = result.error or "I couldn't look up that account right now."
                confidence = 0.4
            return AgentResponse(reply, confidence, tool_results, 0)

        if any(k in lowered for k in ("ticket", "complaint", "issue", "problem")):
            subject = text[:80]
            result = await self._tools.execute(
                "create_ticket",
                ctx,
                subject=subject,
                description=text,
                priority="normal",
            )
            tool_results.append(result.to_dict())
            if result.success:
                reply = f"I created ticket {result.data['ticket_id']}. Our team will follow up shortly."
                confidence = 0.88
            else:
                reply = result.error or "I couldn't create a ticket. Let me transfer you to a human agent."
                return AgentResponse(reply, 0.3, tool_results, 0, True, "tool_failure")
            return AgentResponse(reply, confidence, tool_results, 0)

        kb = await self._tools.execute("knowledge_search", ctx, query=text)
        tool_results.append(kb.to_dict())
        if kb.success and kb.data.get("hits"):
            hit = kb.data["hits"][0]
            reply = f"{hit['title']}: {hit['body']}"
            confidence = 0.82
        else:
            reply = (
                "I can help with orders, tickets, or account questions. "
                "What would you like to do?"
            )
            confidence = 0.7
        return AgentResponse(reply, confidence, tool_results, 0)

    async def _run_gemini(self, state: AgentState, ctx: ToolContext) -> AgentResponse:
        try:
            import google.generativeai as genai
        except ImportError:  # pragma: no cover
            logger.warning("gemini_sdk_missing_fallback_mock")
            return await self._run_mock(state, ctx)

        genai.configure(api_key=self._settings.google_api_key)
        model = genai.GenerativeModel(
            self._settings.gemini_model,
            system_instruction=SYSTEM_PROMPT
            + "\nAvailable tools: "
            + ", ".join(t["name"] for t in self._tools.list_specs()),
        )

        tool_results: list[dict[str, Any]] = []
        lowered = state["user_text"].lower()
        if any(k in lowered for k in ("order", "account", "crm")):
            identifier = self._extract_identifier(state["user_text"])
            kwargs: dict[str, Any] = {}
            if identifier:
                kwargs["identifier"] = identifier
            result = await self._tools.execute("crm_lookup", ctx, **kwargs)
            tool_results.append(result.to_dict())
        elif any(k in lowered for k in ("ticket", "issue", "problem")):
            result = await self._tools.execute(
                "create_ticket",
                ctx,
                subject=state["user_text"][:80],
                description=state["user_text"],
            )
            tool_results.append(result.to_dict())
        else:
            result = await self._tools.execute("knowledge_search", ctx, query=state["user_text"])
            tool_results.append(result.to_dict())

        history_msgs = [
            {"role": "user" if h["role"] == "user" else "model", "parts": [h["content"]]}
            for h in state.get("history", [])
            if h.get("role") in {"user", "assistant"}
        ]
        prompt = (
            f"User said: {state['user_text']}\n"
            f"Tool results: {tool_results}\n"
            "Produce a short spoken reply."
        )
        try:
            chat = model.start_chat(history=history_msgs)
            completion = await chat.send_message_async(prompt)
            text = (completion.text or "").strip()
            confidence = 0.86
        except Exception as exc:  # noqa: BLE001
            logger.exception("gemini_failed", error=str(exc))
            return await self._run_mock(state, ctx)

        if not text:
            text = "Sorry, I didn't catch that. Could you repeat it?"
            confidence = 0.4

        return AgentResponse(text, confidence, tool_results, 0)

    @staticmethod
    def _extract_identifier(text: str) -> str | None:
        email = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
        if email:
            return email.group(0)
        order = re.search(r"\bORD[- ]?\d+\b", text, flags=re.IGNORECASE)
        if order:
            return order.group(0).upper().replace(" ", "-")
        phone = re.search(r"\b\d{10,12}\b", text)
        if phone:
            return phone.group(0)
        return None
