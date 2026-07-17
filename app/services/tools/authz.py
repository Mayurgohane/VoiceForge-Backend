from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config import Settings
from app.core.logging import get_logger
from app.infrastructure.redis_client import RedisClient
from app.services.tools.registry import Tool, ToolResult

logger = get_logger(__name__)


@dataclass(slots=True)
class ToolContext:
    session_id: str
    caller_id: str | None = None
    channel: str = "unknown"


class ToolAuthzError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ToolRateLimiter:
    def __init__(self, redis: RedisClient, settings: Settings) -> None:
        self._redis = redis
        self._settings = settings

    def _key(self, session_id: str, tool_name: str) -> str:
        return f"voiceforge:tool_rl:{session_id}:{tool_name}"

    async def allow(self, session_id: str, tool_name: str) -> bool:
        limit = self._settings.tool_rate_limit_per_minute
        if limit <= 0:
            return True
        count = await self._redis.incr_with_ttl(self._key(session_id, tool_name), 60)
        return count <= limit


class SecuredToolRegistry:
    """Tool registry with session-scoped authz + per-session rate limits."""

    def __init__(
        self,
        *,
        tools: dict[str, Tool],
        redis: RedisClient,
        settings: Settings,
    ) -> None:
        self._tools = tools
        self._limiter = ToolRateLimiter(redis, settings)
        self._settings = settings

    def list_specs(self) -> list[dict[str, str]]:
        return [{"name": t.name, "description": t.description} for t in self._tools.values()]

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    async def execute(self, name: str, context: ToolContext, **kwargs: Any) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(name=name, success=False, data={}, error=f"Unknown tool: {name}")

        if not await self._limiter.allow(context.session_id, name):
            logger.warning(
                "tool_rate_limited",
                session_id=context.session_id,
                tool=name,
            )
            return ToolResult(
                name=name,
                success=False,
                data={},
                error="Rate limit exceeded for this tool. Please try again shortly.",
            )

        try:
            kwargs = self._authorize(name, context, kwargs)
        except ToolAuthzError as exc:
            logger.warning(
                "tool_authz_denied",
                session_id=context.session_id,
                tool=name,
                reason=exc.message,
            )
            return ToolResult(name=name, success=False, data={}, error=exc.message)

        # Always bind session metadata for auditability.
        kwargs.setdefault("session_id", context.session_id)
        if context.caller_id:
            kwargs.setdefault("caller_id", context.caller_id)

        return await tool.run(**kwargs)

    def _authorize(self, name: str, context: ToolContext, kwargs: dict[str, Any]) -> dict[str, Any]:
        if not self._settings.tool_authz_enabled:
            return kwargs

        if name == "crm_lookup":
            identifier = str(
                kwargs.get("identifier") or kwargs.get("phone") or kwargs.get("email") or ""
            ).strip()
            if not identifier or identifier.lower() == "unknown":
                if context.caller_id:
                    kwargs["identifier"] = context.caller_id
                    return kwargs
                raise ToolAuthzError("CRM lookup requires a verified caller identity")

            # Order IDs are not cross-account PII lookups.
            if identifier.upper().startswith("ORD"):
                return kwargs

            if (
                context.caller_id
                and self._settings.tool_strict_caller_bind
                and not self._identity_matches(identifier, context.caller_id)
            ):
                raise ToolAuthzError("CRM lookup identifier does not match authenticated caller")
            return kwargs

        if name == "create_ticket":
            # Tickets must be attributed to the session caller.
            if context.caller_id:
                kwargs["caller_id"] = context.caller_id
            kwargs["session_id"] = context.session_id
            return kwargs

        if name == "knowledge_search":
            return kwargs

        # Unknown tools denied when authz is on.
        raise ToolAuthzError(f"Tool '{name}' is not authorized")

    @staticmethod
    def _identity_matches(identifier: str, caller_id: str) -> bool:
        left = "".join(ch for ch in identifier.lower() if ch.isalnum() or ch in {"@", ".", "+", "-"})
        right = "".join(ch for ch in caller_id.lower() if ch.isalnum() or ch in {"@", ".", "+", "-"})
        if not left or not right:
            return False
        return left == right or left.endswith(right[-10:]) or right.endswith(left[-10:])
