"""Shared configuration and tool lookup for MCP providers."""

import asyncio
import os
import shutil
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from time import perf_counter
from typing import Any

import httpx
from langchain_mcp_adapters.client import MultiServerMCPClient

from tripmate.core.config import Settings
from tripmate.core.exceptions import ConfigurationError, ExternalServiceError
from tripmate.core.logging import get_logger
from tripmate.core.retries import (
    MAX_EXTERNAL_RETRIES,
    is_transient_status_code,
    retry_delay_seconds,
)

logger = get_logger(__name__)

MCP_CONNECT_TIMEOUT_SECONDS = 5.0
MCP_RESPONSE_TIMEOUT_SECONDS = 20.0
MCP_STDIO_READ_TIMEOUT_SECONDS = 20
RETRYABLE_MCP_OPERATIONS = {
    ("tavily", "tavily_search"),
    ("aviationstack", "list_airports"),
    ("aviationstack", "list_airlines"),
}


@dataclass(frozen=True)
class MCPIntegration:
    settings: Settings
    client: MultiServerMCPClient


def _subprocess_env(**updates: str | None) -> dict[str, str]:
    environment = os.environ.copy()
    for key, value in updates.items():
        if value:
            environment[key] = value
    return environment


def create_mcp_integration(settings: Settings) -> MCPIntegration:
    uvx_command = shutil.which("uvx") or "uvx"
    weather_server_path = (
        settings.project_root / "mcp_servers" / "weather_server.py"
    )
    client = MultiServerMCPClient(
        {
            "tavily": {
                "transport": "streamable_http",
                "url": (
                    "https://mcp.tavily.com/mcp/"
                    f"?tavilyApiKey={settings.tavily_api_key or ''}"
                ),
                "timeout": MCP_CONNECT_TIMEOUT_SECONDS,
                "sse_read_timeout": MCP_RESPONSE_TIMEOUT_SECONDS,
                "session_kwargs": {
                    "read_timeout_seconds": timedelta(
                        seconds=MCP_RESPONSE_TIMEOUT_SECONDS,
                    ),
                },
            },
            "aviationstack": {
                "transport": "stdio",
                "command": uvx_command,
                "args": ["aviationstack-mcp"],
                "session_kwargs": {
                    "read_timeout_seconds": timedelta(
                        seconds=MCP_STDIO_READ_TIMEOUT_SECONDS,
                    ),
                },
                "env": _subprocess_env(
                    AVIATION_STACK_API_KEY=settings.aviation_stack_api_key,
                ),
            },
            "weather": {
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(weather_server_path)],
                "session_kwargs": {
                    "read_timeout_seconds": timedelta(
                        seconds=MCP_STDIO_READ_TIMEOUT_SECONDS,
                    ),
                },
                "env": _subprocess_env(
                    OPENWEATHER_API_KEY=settings.openweather_api_key,
                ),
            },
        },
        handle_tool_errors=False,
    )
    return MCPIntegration(settings=settings, client=client)


async def get_server_tool(
    integration: MCPIntegration,
    server_name: str,
    tool_name: str,
) -> Any:
    settings = integration.settings
    if server_name == "tavily":
        settings.require("TAVILY_API_KEY", settings.tavily_api_key)
    elif server_name == "aviationstack":
        settings.require(
            "AVIATION_STACK_API_KEY",
            settings.aviation_stack_api_key,
        )
        if shutil.which("uvx") is None:
            raise ConfigurationError(
                "uvx was not found. Install uv, reopen the terminal, "
                "activate the travel environment, and run "
                "`uvx --version`."
            )
    elif server_name == "weather":
        settings.require(
            "OPENWEATHER_API_KEY",
            settings.openweather_api_key,
        )
        server_path = (
            settings.project_root / "mcp_servers" / "weather_server.py"
        )
        if not server_path.is_file():
            raise ConfigurationError(
                f"Weather MCP server not found: {server_path}"
            )

    tools = await integration.client.get_tools(server_name=server_name)
    tool = next((item for item in tools if item.name == tool_name), None)
    if tool is None:
        raise ExternalServiceError()
    return tool


async def invoke_server_tool(
    integration: MCPIntegration,
    server_name: str,
    tool_name: str,
    tool_args: dict[str, Any] | None = None,
) -> Any:
    try:
        retry_safe = (server_name, tool_name) in RETRYABLE_MCP_OPERATIONS
        tool = await _retry_mcp_operation(
            lambda: get_server_tool(integration, server_name, tool_name),
            retry_safe,
            integration=server_name,
            operation_name=tool_name,
            phase="tool_discovery",
        )
        return await _retry_mcp_operation(
            lambda: tool.ainvoke(tool_args or {}),
            retry_safe,
            integration=server_name,
            operation_name=tool_name,
            phase="tool_invoke",
        )
    except (ConfigurationError, ExternalServiceError):
        raise
    except Exception as exc:
        raise ExternalServiceError() from exc


async def _retry_mcp_operation(
    operation: Callable[[], Awaitable[Any]],
    retry_safe: bool,
    integration: str,
    operation_name: str,
    phase: str,
) -> Any:
    for retry_index in range(MAX_EXTERNAL_RETRIES + 1):
        started_at = perf_counter()
        try:
            result = await operation()
        except Exception as exc:
            duration_ms = round((perf_counter() - started_at) * 1000, 2)
            status_code = (
                exc.response.status_code
                if isinstance(exc, httpx.HTTPStatusError)
                else None
            )
            retryable = retry_safe and _is_transient_mcp_error(exc)
            if not retryable or retry_index == MAX_EXTERNAL_RETRIES:
                logger.error(
                    "External integration operation failed.",
                    extra={
                        "event": "integration.operation.failed",
                        "integration": integration,
                        "operation": operation_name,
                        "phase": phase,
                        "attempt": retry_index + 1,
                        "duration_ms": duration_ms,
                        "status_code": status_code,
                        "outcome": "failure",
                    },
                )
                raise
            headers = (
                exc.response.headers
                if isinstance(exc, httpx.HTTPStatusError)
                else None
            )
            delay = retry_delay_seconds(retry_index, headers)
            if delay is None:
                logger.warning(
                    "External integration retry was not scheduled.",
                    extra={
                        "event": "integration.retry",
                        "integration": integration,
                        "operation": operation_name,
                        "phase": phase,
                        "attempt": retry_index + 1,
                        "duration_ms": duration_ms,
                        "status_code": status_code,
                        "outcome": "retry_abandoned",
                    },
                )
                logger.error(
                    "External integration operation failed.",
                    extra={
                        "event": "integration.operation.failed",
                        "integration": integration,
                        "operation": operation_name,
                        "phase": phase,
                        "attempt": retry_index + 1,
                        "duration_ms": duration_ms,
                        "status_code": status_code,
                        "outcome": "failure",
                    },
                )
                raise
            logger.warning(
                "External integration retry scheduled.",
                extra={
                    "event": "integration.retry",
                    "integration": integration,
                    "operation": operation_name,
                    "phase": phase,
                    "attempt": retry_index + 1,
                    "duration_ms": duration_ms,
                    "status_code": status_code,
                    "outcome": "retry_scheduled",
                },
            )
            await asyncio.sleep(delay)
        else:
            logger.info(
                "External integration operation completed.",
                extra={
                    "event": "integration.operation.completed",
                    "integration": integration,
                    "operation": operation_name,
                    "phase": phase,
                    "attempt": retry_index + 1,
                    "duration_ms": round(
                        (perf_counter() - started_at) * 1000,
                        2,
                    ),
                    "outcome": "success",
                },
            )
            return result

    raise AssertionError("The bounded MCP retry loop exited unexpectedly.")


def _is_transient_mcp_error(exception: Exception) -> bool:
    if isinstance(exception, httpx.HTTPStatusError):
        return is_transient_status_code(exception.response.status_code)
    return isinstance(
        exception,
        (
            httpx.TimeoutException,
            httpx.ConnectError,
            httpx.ReadError,
            httpx.WriteError,
            httpx.RemoteProtocolError,
        ),
    )


async def get_all_tools(integration: MCPIntegration) -> None:
    for server_name in ("tavily", "aviationstack", "weather"):
        try:
            tools = await integration.client.get_tools(
                server_name=server_name,
            )
            tool_names = ", ".join(tool.name for tool in tools) or "no tools"
            print(f"{server_name}: OK -> {tool_names}")
        except Exception:
            logger.error(
                "MCP provider check failed.",
                extra={
                    "event": "integration.health_check.failed",
                    "integration": server_name,
                    "outcome": "failure",
                },
            )
            print(f"{server_name}: FAILED")
