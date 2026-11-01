"""Flight research agent."""

import asyncio
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from tripmate.core.observability import invoke_llm
from tripmate.agents.shared import AgentNode
from tripmate.graph.state import TravelState
from tripmate.integrations.mcp.aviationstack import aviation_mcp_call
from tripmate.integrations.mcp.client import MCPIntegration

FLIGHT_AGENT_PROMPT = """
You are a travel flight expert.

User Query:
{query}

Airport Information:
{airport_data}

Airline Information:
{airline_data}

Generate:
1. Likely departure airport
2. Likely arrival airport
3. Airlines serving this route
4. Typical flight duration
5. Estimated airfare range
6. Peak season pricing warning
7. Booking advice

Return concise travel guidance.
"""


def create_flight_agent(
    llm: Any,
    integration: MCPIntegration,
) -> AgentNode:
    def flight_agent(state: TravelState) -> dict[str, Any]:
        query = state["user_query"]

        airports = asyncio.run(
            aviation_mcp_call(integration, "list_airports")
        )
        airlines = asyncio.run(
            aviation_mcp_call(integration, "list_airlines")
        )
        prompt = FLIGHT_AGENT_PROMPT.format(
            query=query,
            airport_data=str(airports)[:3000],
            airline_data=str(airlines)[:3000],
        )
        response = invoke_llm(
            llm,
            [
                SystemMessage(
                    content="You are an expert travel flight planner."
                ),
                HumanMessage(content=prompt),
            ]
        )
        flight_data = response.content

        return {
            "flight_results": flight_data,
            "messages": [AIMessage(content="Flight recommendations generated")],
            "llm_calls": state.get("llm_calls", 0) + 1,
        }

    return flight_agent
