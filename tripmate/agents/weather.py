"""Weather research agent."""

import asyncio
from typing import Any

from langchain_core.messages import AIMessage

from tripmate.core.observability import invoke_llm
from tripmate.agents.shared import AgentNode
from tripmate.graph.state import TravelState
from tripmate.integrations.mcp.client import MCPIntegration
from tripmate.integrations.mcp.weather import (
    forecast_mcp_search,
    weather_mcp_search,
)


def extract_destination(query: str, llm: Any) -> str:
    prompt = f"""
Extract only the destination city or country from the travel request.

Travel request:
{query}

Return only the destination name.
Do not add any explanation.
"""
    response = invoke_llm(llm, prompt)
    destination = str(response.content).strip()
    if not destination:
        raise ValueError("The destination could not be extracted.")
    return destination


def create_weather_agent(
    llm: Any,
    integration: MCPIntegration,
) -> AgentNode:
    def weather_agent(state: TravelState) -> dict[str, Any]:
        city = extract_destination(state["user_query"], llm)
        weather_data = asyncio.run(
            weather_mcp_search(integration, city)
        )
        forecast_data = asyncio.run(
            forecast_mcp_search(integration, city)
        )
        weather_results = f"""
Current Weather:
{weather_data}

Forecast:
{forecast_data}
"""

        return {
            "weather_results": weather_results,
            "messages": [AIMessage(content="Weather information processed.")],
        }

    return weather_agent
