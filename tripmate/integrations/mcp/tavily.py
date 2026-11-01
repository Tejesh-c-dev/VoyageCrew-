"""Tavily MCP operations."""

from typing import Any

from tripmate.integrations.mcp.client import (
    MCPIntegration,
    invoke_server_tool,
)


async def tavily_mcp_search(
    integration: MCPIntegration,
    query: str,
) -> Any:
    return await invoke_server_tool(
        integration,
        "tavily",
        "tavily_search",
        {"query": query},
    )
