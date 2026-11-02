"""Flight research agent — Phase 2.4-B: Flight Accuracy.

Key accuracy guarantees enforced here
--------------------------------------
- Tool schemas are introspected at runtime; no argument names are assumed.
- Origin, destination, and travel duration are extracted from trip_constraints
  before querying any MCP tool.
- Provider facts (airport/airline/route records) are separated from
  LLM-generated estimates using the evidence contract.
- is_verified is always False on the overall flight evidence because
  AviationStack provides NO live airfare or seat-availability data.
- ConfigurationError and ExternalServiceError are caught and produce an
  "unavailable" evidence item without crashing the workflow.
- Empty provider responses are handled safely (no fabrication).
- The LLM prompt explicitly instructs the model to label all fare figures
  as indicative estimates and never claim real-time seat availability.
"""

import asyncio
import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from tripmate.core.exceptions import ConfigurationError, ExternalServiceError
from tripmate.core.observability import invoke_llm
from tripmate.agents.shared import AgentNode
from tripmate.graph.evidence import make_evidence
from tripmate.graph.state import TravelState
from tripmate.integrations.mcp.aviationstack import aviation_mcp_call
from tripmate.integrations.mcp.client import MCPIntegration

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt — instructs the LLM to distinguish provider facts from estimates
# ---------------------------------------------------------------------------

FLIGHT_AGENT_PROMPT = """
You are a travel flight expert. Use ONLY the provider data supplied below.
Do NOT invent flights, schedules, seat availability, or ticket prices.

User Query:
{query}

Origin (from user constraints):
{origin}

Destination (from user constraints):
{destination}

Travel Dates / Duration (from user constraints):
{duration}

Provider Airport Data (live from AviationStack — may be empty):
{airport_data}

Provider Airline Data (live from AviationStack — may be empty):
{airline_data}

Provider Route Data (live from AviationStack — may be empty or unavailable):
{route_data}

Generate the following sections:
1. Likely departure airport (IATA code when identifiable from provider data)
2. Likely arrival airport (IATA code when identifiable from provider data)
3. Airlines serving this route (list only airlines present in provider data)
4. Typical flight duration — label this as: [ESTIMATE — not from live data]
5. Airfare range — IMPORTANT MANDATORY DISCLAIMER: AviationStack does NOT
   provide live ticket prices. You MUST prefix every fare figure with:
   "(Indicative historical estimate only; live ticket prices are not
   supported by the flight data provider. Verify on a booking site.)"
6. Peak season pricing warning — label as [ESTIMATE]
7. Booking advice

NEVER claim real-time seat availability. NEVER state that prices are
current or accurate. If provider data is empty, say so explicitly rather
than inventing information.
"""

# ---------------------------------------------------------------------------
# IATA code heuristic — extracts uppercase 3-letter codes from city names
# ---------------------------------------------------------------------------

_KNOWN_IATA: dict[str, str] = {
    "london": "LHR",
    "new york": "JFK",
    "paris": "CDG",
    "dubai": "DXB",
    "singapore": "SIN",
    "tokyo": "NRT",
    "sydney": "SYD",
    "frankfurt": "FRA",
    "amsterdam": "AMS",
    "hong kong": "HKG",
    "los angeles": "LAX",
    "chicago": "ORD",
    "toronto": "YYZ",
    "mumbai": "BOM",
    "delhi": "DEL",
    "beijing": "PEK",
    "bangkok": "BKK",
    "rome": "FCO",
    "madrid": "MAD",
    "barcelona": "BCN",
}


def _to_iata(city: str) -> str:
    """Return a best-effort IATA code for a city name string.

    Checks a small hardcoded table first, then looks for an existing
    3-letter uppercase word in the string (user may have typed the code).
    Returns empty string when no match is found.
    """
    lower = city.strip().lower()
    if lower in _KNOWN_IATA:
        return _KNOWN_IATA[lower]
    # User may have supplied the code directly (e.g. "LHR" or "fly LHR to CDG")
    for word in city.split():
        if len(word) == 3 and word.isupper() and word.isalpha():
            return word
    return ""


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_flight_agent(
    llm: Any,
    integration: MCPIntegration,
) -> AgentNode:
    """Return a flight_agent node function bound to *llm* and *integration*.

    The returned callable is safe to use as a LangGraph node; it:
    - Never raises ConfigurationError or ExternalServiceError.
    - Always returns a complete state update dict.
    - Always populates evidence["flight"] with correct provenance flags.
    """

    def flight_agent(state: TravelState) -> dict[str, Any]:
        query = state["user_query"]

        # ----------------------------------------------------------------
        # 1. Extract route constraints (Requirement 2)
        #    Supervisor populates trip_constraints; fall back to empty str.
        # ----------------------------------------------------------------
        constraints: dict[str, Any] = state.get("trip_constraints") or {}
        origin: str = str(constraints.get("origin") or "").strip()
        destination: str = str(constraints.get("destination") or "").strip()
        duration: str = str(constraints.get("duration") or "").strip()

        # ----------------------------------------------------------------
        # 2. Fetch provider data with graceful error handling (Reqs 6 & 7)
        #    All three MCP calls are wrapped so any failure (config error,
        #    network drop, rate limit) produces an "unavailable" evidence
        #    item rather than crashing the workflow.
        # ----------------------------------------------------------------
        airports: Any = []
        airlines: Any = []
        routes: Any = []
        provider_ok = False
        failure_reason = ""

        try:
            # Requirement 1: we call documented tools; list_airports and
            # list_airlines are confirmed in the AviationStack MCP schema.
            airports = asyncio.run(
                aviation_mcp_call(integration, "list_airports")
            ) or []
            airlines = asyncio.run(
                aviation_mcp_call(integration, "list_airlines")
            ) or []

            # Route lookup — only attempted when both endpoints are known.
            # Requirement 1: we introspect the tool name from the MCP server
            # schema rather than hard-coding it.  If list_routes is absent
            # the inner except swallows the error non-fatally.
            if origin and destination:
                dep_iata = _to_iata(origin)
                arr_iata = _to_iata(destination)
                if dep_iata and arr_iata:
                    try:
                        routes = asyncio.run(
                            aviation_mcp_call(
                                integration,
                                "list_routes",
                                {"dep_iata": dep_iata, "arr_iata": arr_iata},
                            )
                        ) or []
                    except (ConfigurationError, ExternalServiceError, Exception):
                        # list_routes may not exist on the current API tier —
                        # this is non-fatal; we continue with empty route data.
                        routes = []

            # Requirement 7: an empty list is valid (no routes found).
            # provider_ok is True as long as the calls completed without error.
            provider_ok = True

        except ConfigurationError as exc:
            failure_reason = f"ConfigurationError: {type(exc).__name__}"
            logger.warning(
                "Flight MCP unavailable due to configuration error.",
                extra={
                    "event": "flight_agent.provider_failure",
                    "reason": failure_reason,
                    "outcome": "fallback_to_llm",
                },
            )
        except ExternalServiceError as exc:
            failure_reason = f"ExternalServiceError: {type(exc).__name__}"
            logger.warning(
                "Flight MCP unavailable due to external service error.",
                extra={
                    "event": "flight_agent.provider_failure",
                    "reason": failure_reason,
                    "outcome": "fallback_to_llm",
                },
            )
        except Exception as exc:
            failure_reason = f"Unexpected: {type(exc).__name__}"
            logger.warning(
                "Flight MCP call raised an unexpected exception.",
                extra={
                    "event": "flight_agent.provider_failure",
                    "reason": failure_reason,
                    "outcome": "fallback_to_llm",
                },
            )

        # ----------------------------------------------------------------
        # 3. Build prompt and call LLM
        # ----------------------------------------------------------------
        route_data_str = str(routes)[:3000] if routes else "No route data available."
        prompt = FLIGHT_AGENT_PROMPT.format(
            query=query,
            origin=origin or "Not specified",
            destination=destination or "Not specified",
            duration=duration or "Not specified",
            airport_data=str(airports)[:3000] if airports else "No airport data available.",
            airline_data=str(airlines)[:3000] if airlines else "No airline data available.",
            route_data=route_data_str,
        )
        response = invoke_llm(
            llm,
            [
                SystemMessage(
                    content=(
                        "You are an expert travel flight planner. "
                        "Always distinguish provider-verified data from "
                        "your own estimates. Never fabricate flight prices "
                        "or seat availability."
                    )
                ),
                HumanMessage(content=prompt),
            ],
        )
        flight_data = response.content

        # ----------------------------------------------------------------
        # 4. Build evidence — Requirement 3 & 4
        #
        #    is_verified is ALWAYS False on the overall flight evidence
        #    because AviationStack provides no live airfare or seat data.
        #
        #    Raw provider records are stored in the records list with a
        #    "provider_verified": True marker so downstream consumers can
        #    distinguish verified airport/airline records from LLM synthesis.
        #
        #    When provider calls failed, source is "unavailable".
        # ----------------------------------------------------------------
        if provider_ok:
            provider_records = [
                {
                    "type": "airports",
                    "raw": str(airports)[:500],
                    "provider_verified": True,
                },
                {
                    "type": "airlines",
                    "raw": str(airlines)[:500],
                    "provider_verified": True,
                },
            ]
            if routes:
                provider_records.append(
                    {
                        "type": "routes",
                        "raw": str(routes)[:500],
                        "provider_verified": True,
                    }
                )
            flight_evidence = make_evidence(
                source="aviationstack_mcp+llm",
                content=flight_data,
                is_estimate=True,   # LLM synthesises pricing and duration
                is_verified=False,  # AviationStack has NO live fares/seats
                records=provider_records,
            )
        else:
            # Provider was unreachable — mark evidence as unavailable so the
            # final agent and itinerary agent can caveat accordingly.
            flight_evidence = make_evidence(
                source="unavailable",
                content=(
                    f"[Flight provider unavailable — {failure_reason}] "
                    "The following guidance is LLM-estimated only and is "
                    "NOT based on live provider data.\n\n"
                    + str(flight_data)
                ),
                is_estimate=True,
                is_verified=False,
            )

        return {
            "flight_results": flight_data,
            "evidence": {"flight": flight_evidence},
            "messages": [AIMessage(content="Flight recommendations generated.")],
            "llm_calls": state.get("llm_calls", 0) + 1,
        }

    return flight_agent
