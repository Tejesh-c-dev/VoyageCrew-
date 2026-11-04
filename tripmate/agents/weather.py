"""Weather research agent."""

import asyncio
from typing import Any

from langchain_core.messages import AIMessage

from tripmate.core.observability import invoke_llm
from tripmate.core.exceptions import ConfigurationError, ExternalServiceError
from tripmate.agents.shared import AgentNode
from tripmate.graph.evidence import make_evidence
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
        unavailable = "Weather information unavailable from OpenWeather."
        try:
            weather_data = asyncio.run(weather_mcp_search(integration, city))
            if weather_data is None:
                weather_data = unavailable
        except (ConfigurationError, ExternalServiceError):
            weather_data = unavailable
        try:
            forecast_data = asyncio.run(forecast_mcp_search(integration, city))
            if forecast_data is None:
                forecast_data = unavailable
        except (ConfigurationError, ExternalServiceError):
            forecast_data = unavailable
        weather_results = f"""
Current Weather:
{weather_data}

Forecast:
{forecast_data}
"""

        # OpenWeather MCP returns live API data (is_verified=True).
        # No LLM synthesis is performed; results are passed through directly
        # (is_estimate=False).
        provider_available = weather_data != unavailable or forecast_data != unavailable
        weather_evidence = make_evidence(
            source="openweather_mcp" if provider_available else "unavailable",
            content=weather_results,
            is_estimate=False,   # live weather API data, not LLM-generated
            is_verified=provider_available,
            records=[
                {"type": "current_weather", "raw": str(weather_data)[:500]},
                {"type": "forecast", "raw": str(forecast_data)[:500]},
            ] if provider_available else [],
        )

        return {
            "weather_results": weather_results,
            "evidence": {"weather": weather_evidence},
            "messages": [AIMessage(content="Weather information processed.")],
        }

    return weather_agent
