from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.services.tools.registry import ToolResult


class TicketTool:
    name = "create_ticket"
    description = "Create a support ticket for the current caller."

    async def run(self, **kwargs: Any) -> ToolResult:
        subject = str(kwargs.get("subject") or "").strip()
        description = str(kwargs.get("description") or "").strip()
        priority = str(kwargs.get("priority") or "normal")
        if not subject:
            return ToolResult(self.name, False, {}, "subject is required")

        ticket_id = f"TKT-{uuid4().hex[:8].upper()}"
        return ToolResult(
            self.name,
            True,
            {
                "ticket_id": ticket_id,
                "subject": subject,
                "description": description,
                "priority": priority,
                "status": "open",
                "caller_id": kwargs.get("caller_id"),
                "session_id": kwargs.get("session_id"),
            },
        )
