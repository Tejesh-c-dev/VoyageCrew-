"""Budget estimation agent."""

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from tripmate.core.observability import invoke_llm
from tripmate.agents.shared import AgentNode
from tripmate.graph.state import TravelState


def create_budget_agent(
    llm: Any,
) -> AgentNode:
    def budget_agent(state: TravelState) -> dict[str, Any]:
        prompt = f"""
Analyze whether this trip is realistic for the user's budget.

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

Return:
1. Estimated cost categories
2. Budget risk areas
3. Money-saving suggestions
4. Overall feasibility

If exact live prices are unavailable, clearly label estimates as approximate.
"""
        response = invoke_llm(
            llm,
            [
                SystemMessage(
                    content="You are a practical travel budget analyst."
                ),
                HumanMessage(content=prompt),
            ]
        )
        return {
            "budget_results": response.content,
            "messages": [AIMessage(content="Budget assessment generated.")],
            "llm_calls": state.get("llm_calls", 0) + 1,
        }

    return budget_agent
