from __future__ import annotations

from typing import Any, TypedDict

from app.domain.models import TranscriptTurn


class AgentState(TypedDict, total=False):
    session_id: str
    user_text: str
    locale: str
    history: list[dict[str, Any]]
    assistant_text: str
    confidence: float
    tool_calls: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    should_handoff: bool
    handoff_reason: str | None


def history_from_turns(turns: list[TranscriptTurn], limit: int = 12) -> list[dict[str, Any]]:
    sliced = turns[-limit:]
    return [{"role": t.role.value, "content": t.content} for t in sliced]


SYSTEM_PROMPT = """You are VoiceForge, a concise real-time voice support agent.
Rules:
- Keep spoken replies under 2 short sentences unless the user asks for detail.
- Prefer tools for factual lookups (orders, tickets, knowledge).
- If unsure or user asks for a human, say you will transfer them.
- Never invent account/order details.
- Do not repeat PII back verbatim when avoidable.
"""
