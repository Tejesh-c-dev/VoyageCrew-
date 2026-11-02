"""Hotel research agent."""

import asyncio
from typing import Any

from langchain_core.messages import AIMessage

from tripmate.agents.shared import AgentNode
from tripmate.graph.evidence import make_evidence
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

        # Extract a representative URL from the Tavily result when available.
        # Tavily returns web-search snippets: these are NOT independently
        # verified by TripMate — we cannot confirm accuracy or recency.
        # is_verified=False, is_estimate=False: web-sourced, not LLM-generated.
        source_url = ""
        raw_text = str(hotel_results)
        if "http" in raw_text:
            for part in raw_text.split():
                if part.startswith("http"):
                    source_url = part.rstrip("',\")>")
                    break

        hotel_evidence = make_evidence(
            source="tavily_mcp",
            content=raw_text,
            is_estimate=False,   # web-sourced content, not LLM-generated
            is_verified=False,   # Tavily web results are NOT independently verified
            source_url=source_url,
        )

        return {
            "hotel_results": hotel_results,
            "evidence": {"hotel": hotel_evidence},
            "messages": [AIMessage(content="Hotel information processed.")],
            "llm_calls": state.get("llm_calls", 0) + 1,
        }

    return hotel_agent
