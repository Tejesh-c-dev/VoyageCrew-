"""Hotel research agent."""

import asyncio
from typing import Any

from langchain_core.messages import AIMessage

from tripmate.agents.shared import AgentNode
from tripmate.graph.state import TravelState
from tripmate.integrations.mcp.client import MCPIntegration
from tripmate.integrations.mcp.tavily import tavily_mcp_search


def create_hotel_agent(
    integration: MCPIntegration,
) -> AgentNode:
    def hotel_agent(state: TravelState) -> dict[str, Any]:
        query = f"Best hotels for {state['user_query']}"
        hotel_results = asyncio.run(
            tavily_mcp_search(integration, query)
        )

        return {
            "hotel_results": hotel_results,
            "messages": [AIMessage(content="Hotel information processed.")],
            "llm_calls": state.get("llm_calls", 0) + 1,
        }

    return hotel_agent
