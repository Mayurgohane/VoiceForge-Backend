from __future__ import annotations

from app.core.config import Settings
from app.services.handoff import HandoffPolicy
from app.services.redaction import PIIRedactor


def test_pii_redaction() -> None:
    redactor = PIIRedactor()
    result = redactor.redact("Reach me at mayur@example.com or 9876543210")
    assert result.redacted
    assert "[REDACTED_EMAIL]" in result.text
    assert "mayur@example.com" not in result.text


def test_handoff_policy_user_request() -> None:
    policy = HandoffPolicy(Settings(handoff_confidence_threshold=0.35))
    decision = policy.evaluate(user_text="please get me a real person", confidence=0.9, turn_count=2)
    assert decision.should_handoff
    assert decision.reason is not None
    assert decision.reason.value == "user_requested"
