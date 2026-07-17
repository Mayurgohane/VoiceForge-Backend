from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol


@dataclass(slots=True)
class ToolResult:
    name: str
    success: bool
    data: dict[str, Any]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Tool(Protocol):
    name: str
    description: str

    async def run(self, **kwargs: Any) -> ToolResult: ...


class ToolRegistry:
    """Plain registry (no authz). Prefer SecuredToolRegistry in production paths."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def as_dict(self) -> dict[str, Tool]:
        return dict(self._tools)

    def list_specs(self) -> list[dict[str, str]]:
        return [{"name": t.name, "description": t.description} for t in self._tools.values()]

    async def execute(self, name: str, **kwargs: Any) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(name=name, success=False, data={}, error=f"Unknown tool: {name}")
        return await tool.run(**kwargs)
