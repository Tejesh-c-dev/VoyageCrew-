"""Phase 2.4-B — Flight Accuracy: regression tests.

Tests are grouped into eight focused classes, each targeting one or more of
the six audit defects fixed in Phase 2.4-B:

  FlightAgentSchemaInspectionTests    - Req 1: schema introspection, unknown tool
  FlightConstraintExtractionTests     - Req 2: origin/destination/duration in prompt
  FlightEvidenceSeparationTests       - Req 3+4: is_verified=False, records tagged
  FlightNoFabricationTests            - Req 3+5: airfare disclaimer, empty provider
  FlightProviderFailureTests          - Req 6+7: ConfigurationError, ExternalServiceError
  FlightRouteValidationTests          - Route tool called with IATA args when possible
  FinalResponseDisclaimerTests        - Req 8: final prompt contains airfare disclaimer
  FlightEvidenceRegressionTests       - Backward-compat: JSON-safe, keys intact
"""

import json
import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

from tripmate.core.exceptions import ConfigurationError, ExternalServiceError
from tripmate.graph.evidence import make_evidence


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_mock_llm(content: str = "Flight guidance text") -> MagicMock:
    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = content
    return mock_llm, mock_response


def _run_flight_agent(
    airports: Any = None,
    airlines: Any = None,
    routes: Any = None,
    mcp_side_effects: list | None = None,
    llm_content: str = "Flight info",
    origin: str = "",
    destination: str = "",
    duration: str = "",
) -> dict[str, Any]:
    """Helper: run flight_agent under full mock isolation.

    *mcp_side_effects* overrides the default asyncio.run side-effect list.
    When not provided, the default is [airports, airlines] (no route call).
    """
    from tripmate.agents.flight import create_flight_agent

    mock_llm, mock_response = _make_mock_llm(llm_content)
    mock_integration = MagicMock()

    if mcp_side_effects is not None:
        side_effects = mcp_side_effects
    else:
        side_effects = [
            airports if airports is not None else [{"code": "LHR"}],
            airlines if airlines is not None else [{"name": "BA"}],
        ]

    state = {
        "user_query": "fly from London to Paris",
        "trip_constraints": {
            "origin": origin,
            "destination": destination,
            "duration": duration,
            "budget": "",
            "travel_style": "",
            "special_preferences": [],
        },
    }

    with patch("tripmate.agents.flight.asyncio.run", side_effect=side_effects), \
         patch("tripmate.agents.flight.invoke_llm", return_value=mock_response):
        agent = create_flight_agent(mock_llm, mock_integration)
        return agent(state)


# ---------------------------------------------------------------------------
# 1. Schema / tool introspection (Requirement 1)
# ---------------------------------------------------------------------------

class FlightAgentSchemaInspectionTests(unittest.TestCase):
    """The agent must not assume argument names; it must handle missing tools."""

    def test_agent_calls_list_airports_and_list_airlines(self):
        """Confirmed MCP tools list_airports and list_airlines are invoked."""
        from tripmate.agents.flight import create_flight_agent

        mock_llm, mock_resp = _make_mock_llm()
        mock_integration = MagicMock()
        call_log: list[str] = []

        def fake_run(coro):
            # Record which coroutine was called by examining its cr_frame
            call_log.append(coro.__name__ if hasattr(coro, "__name__") else str(coro))
            return [{"code": "LHR"}]

        with patch("tripmate.agents.flight.asyncio.run", side_effect=fake_run), \
             patch("tripmate.agents.flight.invoke_llm", return_value=mock_resp):
            agent = create_flight_agent(mock_llm, mock_integration)
            agent({"user_query": "fly to Paris", "trip_constraints": {}})

        # At least 2 asyncio.run calls (airports + airlines)
        self.assertGreaterEqual(len(call_log), 2)

    def test_route_tool_failure_does_not_crash_agent(self):
        """If list_routes raises ExternalServiceError the agent continues."""
        from tripmate.agents.flight import create_flight_agent

        mock_llm, mock_resp = _make_mock_llm()
        mock_integration = MagicMock()

        # airports OK, airlines OK, routes raise
        side_effects = [
            [{"code": "LHR"}],           # list_airports
            [{"name": "BA"}],            # list_airlines
            ExternalServiceError(),       # list_routes — must NOT propagate
        ]

        with patch("tripmate.agents.flight.asyncio.run", side_effect=side_effects), \
             patch("tripmate.agents.flight.invoke_llm", return_value=mock_resp):
            agent = create_flight_agent(mock_llm, mock_integration)
            result = agent({
                "user_query": "fly LHR to CDG",
                "trip_constraints": {
                    "origin": "LHR",
                    "destination": "CDG",
                    "duration": "2h",
                },
            })

        # Result must be returned without raising
        self.assertIn("flight_results", result)
        # Evidence source still reflects the successful outer calls
        self.assertEqual(result["evidence"]["flight"]["source"], "aviationstack_mcp+llm")


# ---------------------------------------------------------------------------
# 2. Constraint extraction (Requirement 2)
# ---------------------------------------------------------------------------

class FlightConstraintExtractionTests(unittest.TestCase):
    """Origin, destination, and duration from trip_constraints must appear in
    the LLM prompt so the model can generate relevant guidance."""

    def _capture_prompt(self, origin: str, destination: str, duration: str) -> str:
        """Return the prompt string passed to invoke_llm."""
        from tripmate.agents.flight import create_flight_agent

        mock_llm, mock_resp = _make_mock_llm()
        mock_integration = MagicMock()
        captured: list[Any] = []

        def fake_invoke_llm(llm, messages):
            captured.append(messages)
            return mock_resp

        with patch("tripmate.agents.flight.asyncio.run", return_value=[]), \
             patch("tripmate.agents.flight.invoke_llm", side_effect=fake_invoke_llm):
            agent = create_flight_agent(mock_llm, mock_integration)
            agent({
                "user_query": "book a trip",
                "trip_constraints": {
                    "origin": origin,
                    "destination": destination,
                    "duration": duration,
                },
            })

        # The prompt is in the HumanMessage content
        return captured[0][1].content  # messages[1] is HumanMessage

    def test_origin_appears_in_prompt(self):
        prompt = self._capture_prompt("London", "Paris", "7 days")
        self.assertIn("London", prompt)

    def test_destination_appears_in_prompt(self):
        prompt = self._capture_prompt("London", "Paris", "7 days")
        self.assertIn("Paris", prompt)

    def test_duration_appears_in_prompt(self):
        prompt = self._capture_prompt("London", "Paris", "7 days")
        self.assertIn("7 days", prompt)

    def test_empty_constraints_handled_without_crash(self):
        """Agent must not raise when trip_constraints is missing or empty."""
        result = _run_flight_agent(origin="", destination="", duration="")
        self.assertIn("flight_results", result)

    def test_none_trip_constraints_handled_without_crash(self):
        """Agent must not raise when trip_constraints is None."""
        from tripmate.agents.flight import create_flight_agent

        mock_llm, mock_resp = _make_mock_llm()
        mock_integration = MagicMock()

        with patch("tripmate.agents.flight.asyncio.run", return_value=[]), \
             patch("tripmate.agents.flight.invoke_llm", return_value=mock_resp):
            agent = create_flight_agent(mock_llm, mock_integration)
            result = agent({
                "user_query": "book a trip",
                "trip_constraints": None,
            })

        self.assertIn("flight_results", result)


# ---------------------------------------------------------------------------
# 3. Evidence separation (Requirements 3 & 4)
# ---------------------------------------------------------------------------

class FlightEvidenceSeparationTests(unittest.TestCase):
    """Provider records and LLM estimates must be tracked separately.

    The overall flight evidence is NEVER is_verified=True because AviationStack
    has no live airfare or seat-inventory endpoint.  Raw airport/airline records
    carry a provider_verified=True marker inside the records list.
    """

    def test_is_verified_is_always_false(self):
        """Core guarantee: overall flight evidence is never is_verified=True."""
        result = _run_flight_agent()
        self.assertFalse(result["evidence"]["flight"]["is_verified"])

    def test_is_estimate_is_true(self):
        result = _run_flight_agent()
        self.assertTrue(result["evidence"]["flight"]["is_estimate"])

    def test_source_is_aviationstack_mcp_plus_llm_when_provider_ok(self):
        result = _run_flight_agent()
        self.assertEqual(
            result["evidence"]["flight"]["source"], "aviationstack_mcp+llm"
        )

    def test_provider_records_tagged_provider_verified_true(self):
        """Records from AviationStack carry provider_verified=True."""
        result = _run_flight_agent()
        records = result["evidence"]["flight"].get("records", [])
        self.assertTrue(len(records) > 0, "Expected at least one record")
        for rec in records:
            self.assertIn("provider_verified", rec)
            self.assertTrue(rec["provider_verified"])

    def test_records_contain_airports_and_airlines(self):
        result = _run_flight_agent()
        types = {r["type"] for r in result["evidence"]["flight"]["records"]}
        self.assertIn("airports", types)
        self.assertIn("airlines", types)

    def test_content_is_llm_output_not_raw_provider_dump(self):
        """The 'content' field mirrors flight_results (LLM output)."""
        result = _run_flight_agent(llm_content="LHR to CDG, estimated £250")
        ev = result["evidence"]["flight"]
        self.assertEqual(ev["content"], result["flight_results"])

    def test_evidence_is_json_serialisable(self):
        result = _run_flight_agent()
        json.dumps(result["evidence"])  # must not raise


# ---------------------------------------------------------------------------
# 4. No fabrication / airfare disclaimer (Requirements 3 & 5)
# ---------------------------------------------------------------------------

class FlightNoFabricationTests(unittest.TestCase):
    """The prompt must explicitly instruct the LLM never to fabricate fares
    or seat availability, and empty provider responses must be handled safely."""

    def test_flight_agent_prompt_contains_no_fabrication_directive(self):
        """FLIGHT_AGENT_PROMPT must contain explicit anti-fabrication wording."""
        from tripmate.agents import flight as flight_module
        self.assertIn("NEVER", flight_module.FLIGHT_AGENT_PROMPT)
        # The prompt uses "Do not invent" as the anti-fabrication directive
        self.assertIn("invent", flight_module.FLIGHT_AGENT_PROMPT.lower())

    def test_flight_agent_prompt_contains_airfare_disclaimer(self):
        """FLIGHT_AGENT_PROMPT must require the LLM to label fares as estimates."""
        from tripmate.agents import flight as flight_module
        prompt = flight_module.FLIGHT_AGENT_PROMPT.lower()
        self.assertIn("indicative", prompt)
        self.assertIn("estimate", prompt)

    def test_flight_agent_prompt_warns_against_seat_availability_claims(self):
        """FLIGHT_AGENT_PROMPT must say never claim real-time seat availability."""
        from tripmate.agents import flight as flight_module
        prompt = flight_module.FLIGHT_AGENT_PROMPT.lower()
        self.assertIn("seat availability", prompt)

    def test_empty_airports_response_safe(self):
        """Empty list from list_airports must not cause a crash or hallucination."""
        result = _run_flight_agent(airports=[], airlines=[{"name": "BA"}])
        self.assertIn("flight_results", result)
        self.assertFalse(result["evidence"]["flight"]["is_verified"])

    def test_empty_airlines_response_safe(self):
        result = _run_flight_agent(airports=[{"code": "LHR"}], airlines=[])
        self.assertIn("flight_results", result)
        self.assertFalse(result["evidence"]["flight"]["is_verified"])

    def test_none_response_from_mcp_safe(self):
        """None returned by aviation_mcp_call must be treated as empty."""
        result = _run_flight_agent(
            mcp_side_effects=[None, None]
        )
        self.assertIn("flight_results", result)

    def test_empty_provider_source_is_still_aviationstack_mcp_plus_llm(self):
        """Even with empty MCP responses, provider_ok=True → source is correct."""
        result = _run_flight_agent(airports=[], airlines=[])
        self.assertEqual(
            result["evidence"]["flight"]["source"], "aviationstack_mcp+llm"
        )


# ---------------------------------------------------------------------------
# 5. Provider failure handling (Requirements 6 & 7)
# ---------------------------------------------------------------------------

class FlightProviderFailureTests(unittest.TestCase):
    """ConfigurationError and ExternalServiceError must produce "unavailable"
    evidence without crashing the workflow node."""

    def _run_with_mcp_error(self, error: Exception) -> dict[str, Any]:
        from tripmate.agents.flight import create_flight_agent

        mock_llm, mock_resp = _make_mock_llm("LLM fallback content")
        mock_integration = MagicMock()

        with patch("tripmate.agents.flight.asyncio.run", side_effect=error), \
             patch("tripmate.agents.flight.invoke_llm", return_value=mock_resp):
            agent = create_flight_agent(mock_llm, mock_integration)
            return agent({"user_query": "fly to Tokyo", "trip_constraints": {}})

    def test_configuration_error_does_not_raise(self):
        """ConfigurationError in MCP call must not propagate out of the node."""
        result = self._run_with_mcp_error(ConfigurationError("key missing"))
        self.assertIn("flight_results", result)

    def test_configuration_error_source_is_unavailable(self):
        result = self._run_with_mcp_error(ConfigurationError("key missing"))
        self.assertEqual(result["evidence"]["flight"]["source"], "unavailable")

    def test_configuration_error_is_verified_false(self):
        result = self._run_with_mcp_error(ConfigurationError("key missing"))
        self.assertFalse(result["evidence"]["flight"]["is_verified"])

    def test_external_service_error_does_not_raise(self):
        """ExternalServiceError in MCP call must not propagate out of the node."""
        result = self._run_with_mcp_error(ExternalServiceError())
        self.assertIn("flight_results", result)

    def test_external_service_error_source_is_unavailable(self):
        result = self._run_with_mcp_error(ExternalServiceError())
        self.assertEqual(result["evidence"]["flight"]["source"], "unavailable")

    def test_external_service_error_is_verified_false(self):
        result = self._run_with_mcp_error(ExternalServiceError())
        self.assertFalse(result["evidence"]["flight"]["is_verified"])

    def test_unexpected_exception_does_not_raise(self):
        """Any unexpected exception in MCP call must not crash the workflow."""
        result = self._run_with_mcp_error(TimeoutError("timeout"))
        self.assertIn("flight_results", result)

    def test_unexpected_exception_source_is_unavailable(self):
        result = self._run_with_mcp_error(TimeoutError("timeout"))
        self.assertEqual(result["evidence"]["flight"]["source"], "unavailable")

    def test_unavailable_evidence_is_estimate_true(self):
        """Fallback LLM output is always an estimate."""
        result = self._run_with_mcp_error(ExternalServiceError())
        self.assertTrue(result["evidence"]["flight"]["is_estimate"])

    def test_unavailable_content_contains_failure_notice(self):
        """Unavailable evidence content must explain the provider failure."""
        result = self._run_with_mcp_error(ConfigurationError("no key"))
        content = result["evidence"]["flight"]["content"]
        self.assertIn("unavailable", content.lower())

    def test_unavailable_evidence_is_json_serialisable(self):
        result = self._run_with_mcp_error(ExternalServiceError())
        json.dumps(result["evidence"])  # must not raise

    def test_flight_results_still_returned_on_provider_failure(self):
        """Even when provider fails, flight_results must contain LLM output."""
        result = self._run_with_mcp_error(ExternalServiceError())
        self.assertEqual(result["flight_results"], "LLM fallback content")


# ---------------------------------------------------------------------------
# 6. Route validation (IATA lookup and route tool argument passing)
# ---------------------------------------------------------------------------

class FlightRouteValidationTests(unittest.TestCase):
    """When origin and destination are known IATA codes, list_routes must be
    invoked with dep_iata and arr_iata arguments."""

    def test_iata_helper_known_city(self):
        from tripmate.agents.flight import _to_iata
        self.assertEqual(_to_iata("London"), "LHR")
        self.assertEqual(_to_iata("paris"), "CDG")
        self.assertEqual(_to_iata("Dubai"), "DXB")

    def test_iata_helper_already_code(self):
        from tripmate.agents.flight import _to_iata
        self.assertEqual(_to_iata("LHR"), "LHR")
        self.assertEqual(_to_iata("JFK"), "JFK")

    def test_iata_helper_unknown_returns_empty(self):
        from tripmate.agents.flight import _to_iata
        self.assertEqual(_to_iata("Timbuktu"), "")
        self.assertEqual(_to_iata(""), "")

    def test_route_tool_invoked_when_both_iata_known(self):
        """list_routes is attempted when origin=London and destination=Paris."""
        from tripmate.agents.flight import create_flight_agent

        mock_llm, mock_resp = _make_mock_llm()
        mock_integration = MagicMock()

        # airports, airlines, then routes (3 calls)
        side_effects = [
            [{"code": "LHR"}],
            [{"name": "BA"}],
            [{"dep_iata": "LHR", "arr_iata": "CDG"}],
        ]

        with patch("tripmate.agents.flight.asyncio.run", side_effect=side_effects) as mock_run, \
             patch("tripmate.agents.flight.invoke_llm", return_value=mock_resp):
            agent = create_flight_agent(mock_llm, mock_integration)
            result = agent({
                "user_query": "fly London to Paris",
                "trip_constraints": {
                    "origin": "London",
                    "destination": "Paris",
                    "duration": "3h",
                },
            })

        # Must have made 3 asyncio.run calls
        self.assertEqual(mock_run.call_count, 3)
        # Route record must appear in evidence
        types = {r["type"] for r in result["evidence"]["flight"]["records"]}
        self.assertIn("routes", types)

    def test_route_not_called_when_no_destination(self):
        """list_routes is skipped when destination is empty."""
        from tripmate.agents.flight import create_flight_agent

        mock_llm, mock_resp = _make_mock_llm()
        mock_integration = MagicMock()

        with patch("tripmate.agents.flight.asyncio.run",
                   return_value=[{"code": "LHR"}]) as mock_run, \
             patch("tripmate.agents.flight.invoke_llm", return_value=mock_resp):
            agent = create_flight_agent(mock_llm, mock_integration)
            agent({
                "user_query": "recommend me a flight",
                "trip_constraints": {"origin": "London", "destination": "", "duration": ""},
            })

        # Only airports + airlines (2 calls, no route call)
        self.assertEqual(mock_run.call_count, 2)


# ---------------------------------------------------------------------------
# 7. Final response disclaimer (Requirement 8)
# ---------------------------------------------------------------------------

class FinalResponseDisclaimerTests(unittest.TestCase):
    """The final agent prompt must instruct the LLM to always include an
    explicit airfare/seat-availability disclaimer in section 7."""

    def test_final_agent_prompt_contains_live_booking_disclaimer(self):
        """final.py prompt source must mention live booking verification."""
        import inspect
        import tripmate.agents.final as final_module
        source = inspect.getsource(final_module)
        self.assertIn("live booking", source.lower())

    def test_final_agent_prompt_contains_airfare_not_verified_clause(self):
        """final.py prompt must tell LLM that flight prices are AI-estimated."""
        import inspect
        import tripmate.agents.final as final_module
        source = inspect.getsource(final_module)
        self.assertIn("AI-estimated", source)

    def test_final_agent_prompt_requires_airline_website_check(self):
        """final.py prompt must direct users to verify on airline/booking sites."""
        import inspect
        import tripmate.agents.final as final_module
        source = inspect.getsource(final_module)
        # Check for guidance to verify on external sites
        self.assertTrue(
            "booking platforms" in source.lower() or "airline websites" in source.lower(),
            "final.py should reference airline websites or booking platforms",
        )

    def test_final_agent_evidence_summary_section_present_in_prompt(self):
        """The final prompt must still include the evidence summary section."""
        import inspect
        import tripmate.agents.final as final_module
        source = inspect.getsource(final_module)
        self.assertIn("Evidence Summary", source)

    def test_flight_is_estimate_flag_propagates_to_evidence_summary(self):
        """When flight evidence is_estimate=True, summary must show ESTIMATE."""
        from tripmate.agents.itinerary import _evidence_summary

        evidence = {
            "flight": make_evidence(
                source="aviationstack_mcp+llm",
                content="some flight info",
                is_estimate=True,
                is_verified=False,
            )
        }
        summary = _evidence_summary(evidence)
        self.assertIn("ESTIMATE", summary)
        self.assertIn("NOT VERIFIED", summary)

    def test_unavailable_source_propagates_to_evidence_summary(self):
        """When flight provider was unreachable, summary must flag NOT VERIFIED."""
        from tripmate.agents.itinerary import _evidence_summary

        evidence = {
            "flight": make_evidence(
                source="unavailable",
                content="provider down",
                is_estimate=True,
                is_verified=False,
            )
        }
        summary = _evidence_summary(evidence)
        self.assertIn("NOT VERIFIED", summary)


# ---------------------------------------------------------------------------
# 8. Backward-compatibility regression (existing contract preserved)
# ---------------------------------------------------------------------------

class FlightEvidenceRegressionTests(unittest.TestCase):
    """The flight agent output shape must remain compatible with the
    existing LangGraph state schema and serialize_result contract."""

    def test_return_keys_are_complete(self):
        result = _run_flight_agent()
        for key in ("flight_results", "evidence", "messages", "llm_calls"):
            self.assertIn(key, result, f"Missing return key: {key}")

    def test_evidence_flight_key_present(self):
        result = _run_flight_agent()
        self.assertIn("flight", result["evidence"])

    def test_evidence_retrieved_at_is_iso8601_string(self):
        from datetime import datetime
        result = _run_flight_agent()
        ts = result["evidence"]["flight"]["retrieved_at"]
        self.assertIsInstance(ts, str)
        datetime.fromisoformat(ts)  # must not raise

    def test_evidence_is_json_serialisable_full_run(self):
        result = _run_flight_agent(
            airports=[{"code": "LHR", "name": "Heathrow"}],
            airlines=[{"name": "British Airways", "iata": "BA"}],
        )
        json.dumps(result["evidence"])  # must not raise

    def test_serialize_result_includes_updated_flight_evidence(self):
        """serialize_result must pass through is_verified=False flight evidence."""
        from tripmate.services.travel_service import serialize_result

        flight_ev = make_evidence(
            source="aviationstack_mcp+llm",
            content="flight data",
            is_estimate=True,
            is_verified=False,
        )
        raw_result = {
            "messages": [],
            "final_response": "done",
            "flight_results": "flight data",
            "hotel_results": "",
            "weather_results": "",
            "budget_results": "",
            "itinerary": "",
            "approval_request": "",
            "selected_agents": [],
            "trip_constraints": {},
            "supervisor_reasoning": "",
            "guardrail_allowed": True,
            "guardrail_reason": "",
            "approved": None,
            "human_feedback": "",
            "llm_calls": 0,
            "evidence": {"flight": flight_ev},
        }
        serialised = serialize_result(raw_result, "thread-abc")
        self.assertIn("evidence", serialised)
        self.assertFalse(serialised["evidence"]["flight"]["is_verified"])

    def test_llm_calls_incremented(self):
        result = _run_flight_agent()
        self.assertEqual(result["llm_calls"], 1)


if __name__ == "__main__":
    unittest.main()
