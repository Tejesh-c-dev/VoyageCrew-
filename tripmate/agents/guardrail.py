"""Travel request guardrail and blocked-request response."""

from typing import Any

from langchain_core.messages import AIMessage

from tripmate.agents.shared import empty_constraints, invoke_text, parse_json_object
from tripmate.core.logging import get_logger
from tripmate.graph.state import TravelState

logger = get_logger(__name__)


def guardrail_agent(state: TravelState, llm: Any) -> dict[str, Any]:
    query = state["user_query"]
    llm_calls = state.get("llm_calls", 0)
    guardrail_prompt = f"""
Determine whether the following request belongs to travel planning or travel
information. Valid requests can include destinations, flights, hotels, weather,
budgets, visas, transportation, sightseeing, food, packing, or itineraries.

Block clearly unrelated requests and requests asking for harmful or illegal
instructions. Do not block a valid travel request merely because some details
are missing.

Return strict JSON only:
{{
  "allowed": true,
  "reason": ""
}}

User request:
{query}
"""

    try:
        guardrail_raw = invoke_text(
            llm,
            "You are the input guardrail for a travel-planning application. "
            "Return strict JSON only.",
            guardrail_prompt,
        )
    except RuntimeError:
        logger.warning(
            "Guardrail model request failed; allowing the request.",
            extra={
                "event": "agent.fallback",
                "agent": "guardrail",
                "outcome": "fallback",
            },
        )
        allowed = True
        guardrail_reason = "Guardrail validation fallback allowed the request."
    else:
        try:
            guardrail_result = parse_json_object(guardrail_raw)
        except ValueError:
            logger.warning(
                "Guardrail response was invalid; allowing the request.",
                extra={
                    "event": "agent.fallback",
                    "agent": "guardrail",
                    "outcome": "fallback",
                },
            )
            allowed = True
            guardrail_reason = (
                "Guardrail validation fallback allowed the request."
            )
        else:
            allowed = bool(guardrail_result.get("allowed", True))
            guardrail_reason = str(
                guardrail_result.get("reason", "")
            ).strip()
            llm_calls += 1

    if not allowed:
        reason = guardrail_reason or (
            "VoyageCrew can only help with travel-planning requests. "
            "Please ask about a destination, flight, hotel, weather, budget, "
            "or itinerary."
        )
        return {
            "guardrail_allowed": False,
            "guardrail_reason": reason,
            "selected_agents": [],
            "trip_constraints": empty_constraints(),
            "supervisor_reasoning": reason,
            "final_response": reason,
            "messages": [
                AIMessage(content=f"Guardrail blocked request: {reason}")
            ],
            "llm_calls": llm_calls,
        }

    return {
        "guardrail_allowed": True,
        "guardrail_reason": guardrail_reason,
        "llm_calls": llm_calls,
    }


def guardrail_blocked_agent(state: TravelState) -> dict[str, Any]:
    reason = state.get("final_response") or state.get("guardrail_reason") or (
        "This request was blocked by the travel input guardrail."
    )
    return {
        "final_response": reason,
        "messages": [AIMessage(content=reason)],
    }
