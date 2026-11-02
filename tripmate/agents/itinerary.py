"""Draft itinerary generation agent."""

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from tripmate.core.observability import invoke_llm
from tripmate.agents.shared import AgentNode
from tripmate.graph.state import TravelState


def _evidence_summary(evidence: dict) -> str:
    """Build a concise provenance note for inclusion in LLM prompts.

    This lets the model automatically caveat estimated figures and distinguish
    them from live data, without the agent author having to hardcode disclaimers
    for every combination of selected agents.
    """
    if not evidence:
        return "No structured evidence available for this run."

    lines = []
    label_map = {
        "aviationstack_mcp+llm": "AviationStack (live records) + LLM synthesis",
        "aviationstack_mcp": "AviationStack (live records)",
        "tavily_mcp": "Tavily web search (not independently verified)",
        "openweather_mcp": "OpenWeather API (live data)",
        "llm": "LLM estimate only (no live data)",
        "unavailable": "Data unavailable",
    }
    for key, item in evidence.items():
        source = item.get("source", "unknown")
        label = label_map.get(source, source)
        is_est = item.get("is_estimate", False)
        is_ver = item.get("is_verified", False)
        flags = []
        if is_est:
            flags.append("ESTIMATE")
        if not is_ver:
            flags.append("NOT VERIFIED")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        retrieved = item.get("retrieved_at", "unknown time")
        lines.append(f"- {key}: {label}{flag_str} (retrieved: {retrieved})")

    return "\n".join(lines)


def create_itinerary_agent(
    llm: Any,
) -> AgentNode:
    def itinerary_agent(state: TravelState) -> dict[str, Any]:
        evidence = state.get("evidence", {})
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

Evidence Summary (data provenance — use this to caveat estimates):
{_evidence_summary(evidence)}

Make the itinerary practical, budget-aware, and easy to follow.
Where evidence shows ESTIMATE or NOT VERIFIED, include an appropriate caveat.
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
