"""Final travel response generation agent."""

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from tripmate.core.observability import invoke_llm
from tripmate.agents.shared import AgentNode
from tripmate.graph.state import TravelState


def create_final_agent(
    llm: Any,
) -> AgentNode:
    def final_agent(state: TravelState) -> dict[str, Any]:
        if state.get("approved", False):
            review_instruction = (
                "The user approved the draft. Preserve its decisions while polishing it."
            )
        else:
            review_instruction = f"""
The user requested a revision. Apply this feedback carefully:
{state.get('human_feedback', '') or 'Improve the draft before finalizing it.'}
"""

        final_prompt = f"""
Generate the final travel response for the user.

Human Review:
{review_instruction}

User Request:
{state['user_query']}

Supervisor Constraints:
{state.get('trip_constraints', {})}

Flights:
{state.get('flight_results', '')}

Hotels:
{state.get('hotel_results', '')}

Weather:
{state.get('weather_results', '')}

Budget Analysis:
{state.get('budget_results', '')}

Draft Itinerary:
{state.get('itinerary', '')}

Format the final answer beautifully using these sections:
1. Trip Summary
2. Flight Information
3. Hotel Suggestions
4. Weather Information
5. Day-by-Day Itinerary
6. Estimated Budget
7. Final Recommendations

Important:
- Be clear and practical.
- Mention that live flight APIs may not provide ticket prices when pricing is unavailable.
- Include weather-based travel advice.
- Keep the response useful for real travel planning.
- Incorporate the human feedback when revision was requested.
"""
        response = invoke_llm(
            llm,
            [
                SystemMessage(
                    content="You are a professional AI travel booking assistant."
                ),
                HumanMessage(content=final_prompt),
            ]
        )
        return {
            "final_response": response.content,
            "messages": [response],
            "llm_calls": state.get("llm_calls", 0) + 1,
        }

    return final_agent
