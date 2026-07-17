from __future__ import annotations

from app.services.tools.crm import CRMLookupTool
from app.services.tools.knowledge import KnowledgeSearchTool
from app.services.tools.registry import ToolRegistry
from app.services.tools.tickets import TicketTool


def build_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(CRMLookupTool())
    registry.register(TicketTool())
    registry.register(KnowledgeSearchTool())
    return registry
