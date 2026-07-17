from __future__ import annotations


class VoiceForgeError(Exception):
    """Base application error."""

    def __init__(self, message: str, *, code: str = "voiceforge_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class NotFoundError(VoiceForgeError):
    def __init__(self, message: str = "Resource not found") -> None:
        super().__init__(message, code="not_found")


class UnauthorizedError(VoiceForgeError):
    def __init__(self, message: str = "Unauthorized") -> None:
        super().__init__(message, code="unauthorized")


class SessionError(VoiceForgeError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="session_error")


class ProviderError(VoiceForgeError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="provider_error")


class HandoffRequired(VoiceForgeError):
    def __init__(self, message: str = "Human handoff required") -> None:
        super().__init__(message, code="handoff_required")
