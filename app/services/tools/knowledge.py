from __future__ import annotations

from typing import Any

from app.services.tools.registry import ToolResult

_KB = [
    {
        "id": "kb-1",
        "title": "Track an order",
        "body": "Orders usually ship within 2 business days. Use order ID to check status.",
        "tags": ["shipping", "order"],
    },
    {
        "id": "kb-2",
        "title": "Reset password",
        "body": "Visit account settings, choose reset password, and follow the email link.",
        "tags": ["account", "password"],
    },
    {
        "id": "kb-3",
        "title": "Speak to a human",
        "body": "You can request a human agent at any time and we will transfer the call.",
        "tags": ["handoff", "support"],
    },
]


class KnowledgeSearchTool:
    name = "knowledge_search"
    description = "Search internal knowledge base articles."

    async def run(self, **kwargs: Any) -> ToolResult:
        query = str(kwargs.get("query") or "").lower().strip()
        if not query:
            return ToolResult(self.name, False, {}, "query is required")

        hits = []
        for article in _KB:
            hay = f"{article['title']} {article['body']} {' '.join(article['tags'])}".lower()
            if any(token in hay for token in query.split()):
                hits.append(article)
        return ToolResult(self.name, True, {"query": query, "hits": hits[:3]})
