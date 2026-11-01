"""Draft itinerary generation agent."""

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from tripmate.core.observability import invoke_llm
from tripmate.agents.shared import AgentNode
from tripmate.graph.state import TravelState


def create_itinerary_agent(
    llm: Any,
) -> AgentNode:
    def itinerary_agent(state: TravelState) -> dict[str, Any]:
        prompt = f"""
Create a complete travel itinerary.

User Query:
{state['user_query']}

Trip Constraints:
{state.get('trip_constraints', {})}

Flight Results:
{state.get('flight_results', '')}

Hotel Results:
{state.get('hotel_results', '')}

Weather Results:
{state.get('weather_results', '')}

Budget Results:
{state.get('budget_results', '')}

Make the itinerary practical, budget-aware, and easy to follow.
Create a clear draft that is ready for human review.
"""
        response = invoke_llm(
            llm,
            [
                SystemMessage(content="You are an expert travel planner."),
                HumanMessage(content=prompt),
            ]
        )
        approval_request = (
            "Please review the generated draft itinerary. Approve it to create "
            "the final polished plan, or provide feedback for revision."
        )
        return {
            "itinerary": response.content,
            "approval_request": approval_request,
            "messages": [
                AIMessage(content="Draft itinerary created for human review.")
            ],
            "llm_calls": state.get("llm_calls", 0) + 1,
        }

    return itinerary_agent
