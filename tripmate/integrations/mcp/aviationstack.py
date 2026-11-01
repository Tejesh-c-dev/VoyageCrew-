"""AviationStack MCP operations."""

from typing import Any

from tripmate.integrations.mcp.client import (
    MCPIntegration,
    invoke_server_tool,
)


async def aviation_mcp_call(
    integration: MCPIntegration,
    tool_name: str,
    tool_args: dict[str, Any] | None = None,
) -> Any:
    return await invoke_server_tool(
        integration,
        "aviationstack",
        tool_name,
        tool_args,
    )
