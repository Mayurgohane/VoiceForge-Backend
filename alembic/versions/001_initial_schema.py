"""Initial schema: voice_sessions, call_events, audit_logs.

Revision ID: 001_initial
Revises:
Create Date: 2026-07-17
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "voice_sessions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("caller_id", sa.String(length=128), nullable=True),
        sa.Column("locale", sa.String(length=16), nullable=False, server_default="en-US"),
        sa.Column("handoff_reason", sa.String(length=64), nullable=True),
        sa.Column("turn_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("transcript_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_voice_sessions_channel", "voice_sessions", ["channel"])
    op.create_index("ix_voice_sessions_status", "voice_sessions", ["status"])

    op.create_table(
        "call_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_call_events_session_id", "call_events", ["session_id"])
    op.create_index("ix_call_events_event_type", "call_events", ["event_type"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_audit_logs_session_id", "audit_logs", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_session_id", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index("ix_call_events_event_type", table_name="call_events")
    op.drop_index("ix_call_events_session_id", table_name="call_events")
    op.drop_table("call_events")
    op.drop_index("ix_voice_sessions_status", table_name="voice_sessions")
    op.drop_index("ix_voice_sessions_channel", table_name="voice_sessions")
    op.drop_table("voice_sessions")
