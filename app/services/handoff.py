from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings
from app.domain.enums import HandoffReason


@dataclass(slots=True)
class HandoffDecision:
    should_handoff: bool
    reason: HandoffReason | None = None
    message: str | None = None


class HandoffPolicy:
    def __init__(self, settings: Settings) -> None:
        self._threshold = settings.handoff_confidence_threshold

    def evaluate(
        self,
        *,
        user_text: str,
        confidence: float | None,
        turn_count: int,
        tool_failed: bool = False,
    ) -> HandoffDecision:
        lowered = user_text.lower()
        if any(phrase in lowered for phrase in ("speak to a human", "real person", "agent please")):
            return HandoffDecision(
                True,
                HandoffReason.USER_REQUESTED,
                "Connecting you to a human specialist now.",
            )
        if tool_failed:
            return HandoffDecision(
                True,
                HandoffReason.TOOL_FAILURE,
                "I hit a system issue and will transfer you to a human agent.",
            )
        if confidence is not None and confidence < self._threshold:
            return HandoffDecision(
                True,
                HandoffReason.LOW_CONFIDENCE,
                "I want to make sure you get accurate help — transferring you now.",
            )
        if turn_count >= 20:
            return HandoffDecision(
                True,
                HandoffReason.MAX_TURNS,
                "This conversation needs a human specialist. Transferring now.",
            )
        return HandoffDecision(False)
