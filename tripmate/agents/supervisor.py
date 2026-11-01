"""Supervisor request classification and specialist selection."""

from typing import Any

from langchain_core.messages import AIMessage

from tripmate.agents.shared import (
    empty_constraints,
    invoke_text,
    parse_json_object,
)
from tripmate.core.logging import get_logger
from tripmate.graph.routing import AGENT_ORDER, KNOWN_AGENTS
from tripmate.graph.state import TravelState

logger = get_logger(__name__)


def supervisor_agent(state: TravelState, llm: Any) -> dict[str, Any]:
    query = state["user_query"]
    llm_calls = state.get("llm_calls", 0)
    supervisor_prompt = f"""
You are the supervisor of a multi-agent travel-planning system.
Choose only the specialist agents needed for the request.

Available agents:
- flight_agent: flights, airports, airlines, routes, airfare, or booking advice
- hotel_agent: hotels, accommodation, neighborhoods, or places to stay
- weather_agent: weather, climate, season, forecast, or packing advice
- budget_agent: cost, affordability, price limits, or budget feasibility
- itinerary_agent: creates the integrated travel plan and must always be included

Return strict JSON only using this schema:
{{
  "selected_agents": ["flight_agent", "hotel_agent", "weather_agent", "budget_agent", "itinerary_agent"],
  "trip_constraints": {{
    "destination": "",
    "origin": "",
    "duration": "",
    "budget": "",
    "travel_style": "",
    "special_preferences": []
  }},
  "reasoning": ""
}}

User request:
{query}
"""

    try:
        supervisor_raw = invoke_text(
            llm,
            "You route work to travel specialist agents. Return strict JSON only.",
            supervisor_prompt,
        )
    except RuntimeError:
        logger.warning(
            "Supervisor model request failed; using all agents.",
            extra={
                "event": "agent.fallback",
                "agent": "supervisor",
                "outcome": "fallback",
            },
        )
        selected = AGENT_ORDER.copy()
        constraints = empty_constraints()
        reasoning = (
            "Supervisor validation failed, so the original full travel "
            "workflow was selected as a safe fallback."
        )
    else:
        try:
            parsed = parse_json_object(supervisor_raw)
            requested_agents = parsed.get("selected_agents", [])
            if not isinstance(requested_agents, list):
                raise ValueError("The selected agent list is invalid.")
        except ValueError:
            logger.warning(
                "Supervisor response was invalid; using all agents.",
                extra={
                    "event": "agent.fallback",
                    "agent": "supervisor",
                    "outcome": "fallback",
                },
            )
            selected = AGENT_ORDER.copy()
            constraints = empty_constraints()
            reasoning = (
                "Supervisor parsing failed, so the original full travel "
                "workflow was selected as a safe fallback."
            )
        else:
            selected = [
                name
                for name in AGENT_ORDER
                if name in requested_agents and name in KNOWN_AGENTS
            ]
            if "itinerary_agent" not in selected:
                selected.append("itinerary_agent")

            constraints = empty_constraints()
            parsed_constraints = parsed.get("trip_constraints", {})
            if isinstance(parsed_constraints, dict):
                constraints.update(parsed_constraints)

            reasoning = str(parsed.get("reasoning", "")).strip()
            llm_calls += 1

    return {
        "guardrail_allowed": True,
        "selected_agents": selected,
        "trip_constraints": constraints,
        "supervisor_reasoning": reasoning,
        "messages": [AIMessage(content="Supervisor created the agent plan.")],
        "llm_calls": llm_calls,
    }
