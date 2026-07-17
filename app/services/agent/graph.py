from __future__ import annotations

from typing import Any, TypedDict

from app.services.tools.registry import ToolRegistry

# Optional LangGraph wiring.
# Primary path uses VoiceAgent in runtime.py; this factory is for LangGraph teams.


class GraphState(TypedDict, total=False):
    user_text: str
    reply: str
    tool_data: dict[str, Any]


def build_langgraph_agent(tools: ToolRegistry):  # noqa: ANN201
    try:
        from langgraph.graph import END, StateGraph
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("langgraph is not installed") from exc

    async def reason_node(state: GraphState) -> GraphState:
        text = state.get("user_text", "")
        kb = await tools.execute("knowledge_search", query=text)
        return {
            "user_text": text,
            "tool_data": kb.data if kb.success else {},
            "reply": "",
        }

    async def respond_node(state: GraphState) -> GraphState:
        hits = (state.get("tool_data") or {}).get("hits") or []
        if hits:
            reply = f"{hits[0]['title']}: {hits[0]['body']}"
        else:
            reply = "How can I help with your account, order, or ticket today?"
        return {**state, "reply": reply}

    graph = StateGraph(GraphState)
    graph.add_node("reason", reason_node)
    graph.add_node("respond", respond_node)
    graph.set_entry_point("reason")
    graph.add_edge("reason", "respond")
    graph.add_edge("respond", END)
    return graph.compile()
