"""Final travel response generation agent."""

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from tripmate.core.observability import invoke_llm
from tripmate.agents.shared import AgentNode
from tripmate.agents.itinerary import _evidence_summary
from tripmate.graph.state import TravelState


def create_final_agent(
    llm: Any,
) -> AgentNode:
    def final_agent(state: TravelState) -> dict[str, Any]:
        evidence = state.get("evidence", {})
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

Evidence Summary (data provenance — use this to generate accurate disclaimers):
{_evidence_summary(evidence)}

Format the final answer beautifully using these sections:
1. Trip Summary
2. Flight Information
3. Hotel Suggestions
4. Weather Information
5. Day-by-Day Itinerary
6. Estimated Budget
7. Data Sources & Disclaimers
8. Final Recommendations

Important:
- Be clear and practical.
- In section 7 (Data Sources & Disclaimers), list each data source from the
  Evidence Summary and note whether figures are estimates or live data.
- Flag budget figures and flight pricing as LLM estimates when evidence shows
  is_estimate=True.
- Flag hotel results as web-sourced and not independently verified.
- Include weather-based travel advice.
- In section 7, always include the following sentence verbatim:
  "Flight prices and real-time seat availability shown above are
  AI-estimated and have NOT been verified against live booking
  systems. Verify airfares and availability directly on airline
  websites or booking platforms before purchasing."
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
