from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from app.api.deps import get_container
from app.core.security import require_api_key
from app.domain.models import VoiceSession
from app.schemas.session import CreateSessionRequest, SessionListResponse, SessionResponse
from app.schemas.voice import TextTurnRequest

router = APIRouter(prefix="/sessions", tags=["sessions"], dependencies=[Depends(require_api_key)])


def _to_response(session: VoiceSession) -> SessionResponse:
    return SessionResponse(
        id=session.id,
        channel=session.channel,
        status=session.status,
        caller_id=session.caller_id,
        locale=session.locale,
        turn_count=len(session.turns),
        created_at=session.created_at,
        updated_at=session.updated_at,
        ended_at=session.ended_at,
        handoff_reason=session.handoff_reason,
        metadata=session.metadata,
    )


@router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(request: Request, body: CreateSessionRequest) -> SessionResponse:
    container = get_container(request)
    session = await container.sessions.create(
        channel=body.channel,
        caller_id=body.caller_id,
        locale=body.locale,
        metadata=body.metadata,
    )
    return _to_response(session)


@router.get("", response_model=SessionListResponse)
async def list_sessions(request: Request, limit: int = 50) -> SessionListResponse:
    container = get_container(request)
    items = await container.sessions.list_sessions(limit=min(limit, 200))
    return SessionListResponse(items=[_to_response(s) for s in items], total=len(items))


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str, request: Request) -> SessionResponse:
    container = get_container(request)
    session = await container.sessions.get(session_id)
    return _to_response(session)


@router.post("/{session_id}/end", response_model=SessionResponse)
async def end_session(session_id: str, request: Request) -> SessionResponse:
    container = get_container(request)
    session = await container.sessions.end(session_id)
    return _to_response(session)


@router.post("/{session_id}/turns/text")
async def text_turn(session_id: str, request: Request, body: TextTurnRequest) -> dict:
    """Drive a full turn without audio — useful for integration tests and demos."""
    container = get_container(request)
    events = await container.pipeline.handle_user_text(session_id, body.text)
    return {
        "session_id": session_id,
        "events": [e.model_dump(mode="json") for e in events],
    }
