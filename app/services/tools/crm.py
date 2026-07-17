from __future__ import annotations

from typing import Any

from app.services.tools.registry import ToolResult


class CRMLookupTool:
    name = "crm_lookup"
    description = "Look up a customer profile by phone or email."

    async def run(self, **kwargs: Any) -> ToolResult:
        identifier = str(kwargs.get("identifier") or kwargs.get("phone") or kwargs.get("email") or "")
        if not identifier:
            return ToolResult(self.name, False, {}, "identifier is required")

        # Demo CRM store — replace with real CRM API client.
        profile = {
            "identifier": identifier,
            "name": "Alex Customer",
            "tier": "gold",
            "open_tickets": 1,
            "last_order_id": "ORD-10482",
            "last_order_status": "shipped",
        }
        return ToolResult(self.name, True, profile)
