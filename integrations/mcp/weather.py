"""Weather MCP operations."""

from typing import Any

from tripmate.integrations.mcp.client import (
    MCPIntegration,
    invoke_server_tool,
)


async def weather_mcp_search(
    integration: MCPIntegration,
    city: str,
) -> Any:
    return await invoke_server_tool(
        integration,
        "weather",
        "get_current_weather",
        {"city": city},
    )


async def forecast_mcp_search(
    integration: MCPIntegration,
    city: str,
) -> Any:
    return await invoke_server_tool(
        integration,
        "weather",
        "get_forecast",
        {"city": city},
    )
