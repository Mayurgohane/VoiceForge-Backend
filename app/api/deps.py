from __future__ import annotations

from fastapi import Request

from app.api.container import AppContainer
from app.core.exceptions import VoiceForgeError


def get_container(request: Request) -> AppContainer:
    container = getattr(request.app.state, "container", None)
    if container is None:
        raise VoiceForgeError("Application container is not initialized")
    return container
