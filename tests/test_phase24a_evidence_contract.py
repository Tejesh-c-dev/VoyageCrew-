"""Phase 2.4-A — Evidence Contract: comprehensive regression tests.

Tests are grouped into focused classes:

  EvidenceSchemaTests       - TypedDict schema, JSON serialisability, field types
  MergeReducerTests         - merge_evidence reducer correctness and edge cases
  MakeEvidenceTests         - factory output, ISO-8601 timestamps, records field
  FlightAgentEvidenceTests  - flight agent provenance fields
  HotelAgentEvidenceTests   - hotel agent provenance, Tavily not-verified rule
  WeatherAgentEvidenceTests - weather agent provenance, live-data flags
  BudgetAgentEvidenceTests  - budget agent, llm-only provenance
  ItineraryEvidenceSummary  - _evidence_summary helper correctness
  SerialiseResultTests      - evidence key in serialize_result, backward compat
  CheckpointCompatTests     - all evidence values are JSON-primitive (JSONB safe)
  BackwardCompatTests       - existing state fields untouched; empty evidence OK
"""

import json
import unittest
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from tripmate.graph.evidence import (
    AgentEvidence,
    EvidenceItem,
    SourceLabel,
    make_evidence,
    merge_evidence,
)
from tripmate.agents.itinerary import _evidence_summary
from tripmate.services.travel_service import serialize_result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_evidence(source: SourceLabel = "llm") -> EvidenceItem:
    return make_evidence(
        source=source,
        content="test content",
        is_estimate=True,
        is_verified=False,
    )


def _minimal_result(**overrides: Any) -> dict[str, Any]:
    """Build the minimal dict shape that serialize_result expects."""
    base: dict[str, Any] = {
        "messages": [],
        "final_response": "done",
        "flight_results": "",
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
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. Evidence schema
# ---------------------------------------------------------------------------

class EvidenceSchemaTests(unittest.TestCase):
    def test_evidence_item_is_dict_subtype(self):
        """EvidenceItem created by make_evidence is a plain dict (no custom class)."""
        item = _minimal_evidence()
        self.assertIsInstance(item, dict)

    def test_all_required_fields_present(self):
        item = make_evidence(
            source="openweather_mcp",
            content="16°C, partly cloudy",
            is_estimate=False,
            is_verified=True,
        )
        for field in ("source", "content", "is_estimate", "is_verified", "retrieved_at"):
            self.assertIn(field, item, f"Missing field: {field}")

    def test_source_url_defaults_to_empty_string(self):
        item = _minimal_evidence()
        self.assertEqual(item.get("source_url", ""), "")

    def test_records_absent_when_not_provided(self):
        item = _minimal_evidence()
        self.assertNotIn("records", item)

    def test_records_present_when_provided(self):
        item = make_evidence(
            source="aviationstack_mcp+llm",
            content="flight info",
            is_estimate=True,
            is_verified=True,
            records=[{"type": "airports", "raw": "LHR"}],
        )
        self.assertIn("records", item)
        self.assertEqual(item["records"][0]["type"], "airports")

    def test_is_estimate_and_is_verified_are_booleans(self):
        item = make_evidence(
            source="llm",
            content="budget estimate",
            is_estimate=True,
            is_verified=False,
        )
        self.assertIs(type(item["is_estimate"]), bool)
        self.assertIs(type(item["is_verified"]), bool)

    def test_content_is_string(self):
        item = _minimal_evidence()
        self.assertIsInstance(item["content"], str)

    def test_source_field_is_string(self):
        item = _minimal_evidence("tavily_mcp")
        self.assertIsInstance(item["source"], str)


# ---------------------------------------------------------------------------
# 2. Merge reducer
# ---------------------------------------------------------------------------

class MergeReducerTests(unittest.TestCase):
    def test_merges_disjoint_keys(self):
        """Two agents writing different keys both survive the merge."""
        a: AgentEvidence = {"flight": _minimal_evidence("aviationstack_mcp+llm")}
        b: AgentEvidence = {"hotel": _minimal_evidence("tavily_mcp")}
        merged = merge_evidence(a, b)
        self.assertIn("flight", merged)
        self.assertIn("hotel", merged)

    def test_all_four_agent_keys_survive_sequential_merges(self):
        ev: AgentEvidence = {}
        for key, source in [
            ("flight", "aviationstack_mcp+llm"),
            ("hotel", "tavily_mcp"),
            ("weather", "openweather_mcp"),
            ("budget", "llm"),
        ]:
            ev = merge_evidence(ev, {key: _minimal_evidence(source)})
        for key in ("flight", "hotel", "weather", "budget"):
            self.assertIn(key, ev, f"Key lost after sequential merges: {key}")

    def test_later_write_wins_on_same_key(self):
        """If the same agent runs twice, the newer value wins (expected behaviour)."""
        a: AgentEvidence = {"flight": make_evidence(
            source="aviationstack_mcp+llm", content="first", is_estimate=True, is_verified=True
        )}
        b: AgentEvidence = {"flight": make_evidence(
            source="aviationstack_mcp+llm", content="second", is_estimate=True, is_verified=True
        )}
        merged = merge_evidence(a, b)
        self.assertEqual(merged["flight"]["content"], "second")

    def test_merge_with_empty_existing(self):
        update: AgentEvidence = {"budget": _minimal_evidence("llm")}
        merged = merge_evidence({}, update)
        self.assertIn("budget", merged)

    def test_merge_with_empty_update(self):
        existing: AgentEvidence = {"weather": _minimal_evidence("openweather_mcp")}
        merged = merge_evidence(existing, {})
        self.assertIn("weather", merged)

    def test_merge_does_not_mutate_inputs(self):
        a: AgentEvidence = {"flight": _minimal_evidence()}
        b: AgentEvidence = {"hotel": _minimal_evidence()}
        _ = merge_evidence(a, b)
        self.assertNotIn("hotel", a)
        self.assertNotIn("flight", b)


# ---------------------------------------------------------------------------
# 3. make_evidence factory
# ---------------------------------------------------------------------------

class MakeEvidenceTests(unittest.TestCase):
    def test_retrieved_at_is_iso8601_string(self):
        item = _minimal_evidence()
        retrieved = item["retrieved_at"]
        self.assertIsInstance(retrieved, str)
        # Must parse without error
        parsed = datetime.fromisoformat(retrieved)
        self.assertIsNotNone(parsed)

    def test_retrieved_at_is_utc(self):
        item = _minimal_evidence()
        retrieved = item["retrieved_at"]
        # ISO-8601 UTC strings end with +00:00 or contain Z
        self.assertTrue(
            retrieved.endswith("+00:00") or "Z" in retrieved,
            f"retrieved_at is not UTC: {retrieved}",
        )

    def test_retrieved_at_is_never_datetime_object(self):
        """Critical: datetime objects break PostgresSaver JSONB codec."""
        item = _minimal_evidence()
        self.assertNotIsInstance(item["retrieved_at"], datetime)

    def test_source_url_stored_when_provided(self):
        item = make_evidence(
            source="tavily_mcp",
            content="hotel list",
            is_estimate=False,
            is_verified=False,
            source_url="https://example.com/hotels",
        )
        self.assertEqual(item["source_url"], "https://example.com/hotels")

    def test_all_valid_source_labels_accepted(self):
        sources = [
            "aviationstack_mcp",
            "aviationstack_mcp+llm",
            "tavily_mcp",
            "openweather_mcp",
            "llm",
            "unavailable",
        ]
        for source in sources:
            item = make_evidence(
                source=source,
                content="test",
                is_estimate=False,
                is_verified=False,
            )
            self.assertEqual(item["source"], source)

    def test_records_are_list_of_dicts(self):
        item = make_evidence(
            source="openweather_mcp",
            content="weather",
            is_estimate=False,
            is_verified=True,
            records=[{"type": "current", "raw": "15C"}],
        )
        self.assertIsInstance(item["records"], list)
        self.assertIsInstance(item["records"][0], dict)


# ---------------------------------------------------------------------------
# 4. Flight agent evidence
# ---------------------------------------------------------------------------

class _NoFlightCoroutineMixin:
    def setUp(self):
        super().setUp()
        patcher = patch(
            "tripmate.agents.flight.aviation_mcp_call",
            new=MagicMock(return_value=None),
        )
        patcher.start()
        self.addCleanup(patcher.stop)


class FlightAgentEvidenceTests(_NoFlightCoroutineMixin, unittest.TestCase):
    def _run_flight_agent(self, airports=None, airlines=None, llm_content="Flight info"):
        from tripmate.agents.flight import create_flight_agent

        mock_llm = MagicMock()
        mock_llm_response = MagicMock()
        mock_llm_response.content = llm_content
        mock_integration = MagicMock()

        with patch("tripmate.agents.flight.asyncio.run", side_effect=[
            airports or [{"code": "LHR"}],
            airlines or [{"name": "BA"}],
        ]), patch("tripmate.agents.flight.invoke_llm", return_value=mock_llm_response):
            agent = create_flight_agent(mock_llm, mock_integration)
            return agent({"user_query": "fly to London"})

    def test_evidence_key_present_in_return(self):
        result = self._run_flight_agent()
        self.assertIn("evidence", result)
        self.assertIn("flight", result["evidence"])

    def test_source_is_aviationstack_mcp_plus_llm(self):
        result = self._run_flight_agent()
        self.assertEqual(result["evidence"]["flight"]["source"], "aviationstack_mcp+llm")

    def test_is_estimate_is_true(self):
        result = self._run_flight_agent()
        self.assertTrue(result["evidence"]["flight"]["is_estimate"])

    def test_is_verified_is_false_pricing_not_backed_by_live_data(self):
        """Phase 2.4-B: flight evidence must NOT claim is_verified=True.

        AviationStack provides airport/airline reference data but has NO live
        airfare, seat inventory, or real-time schedule endpoint.  Marking the
        overall flight result as verified would misrepresent LLM-synthesised
        pricing estimates as authoritative provider facts.
        """
        result = self._run_flight_agent()
        self.assertFalse(result["evidence"]["flight"]["is_verified"])

    def test_content_matches_flight_results(self):
        result = self._run_flight_agent(llm_content="Fly via LHR, ~£350")
        self.assertEqual(result["evidence"]["flight"]["content"], result["flight_results"])

    def test_records_contains_airports_and_airlines(self):
        result = self._run_flight_agent()
        records = result["evidence"]["flight"]["records"]
        types = {r["type"] for r in records}
        self.assertIn("airports", types)
        self.assertIn("airlines", types)

    def test_records_are_json_serialisable(self):
        result = self._run_flight_agent()
        json.dumps(result["evidence"])  # must not raise

    def test_retrieved_at_is_iso8601_string(self):
        result = self._run_flight_agent()
        ts = result["evidence"]["flight"]["retrieved_at"]
        self.assertIsInstance(ts, str)
        datetime.fromisoformat(ts)  # must not raise


# ---------------------------------------------------------------------------
# 5. Hotel agent evidence
# ---------------------------------------------------------------------------

class HotelAgentEvidenceTests(unittest.TestCase):
    def setUp(self):
        patcher = patch(
            "tripmate.agents.hotel.tavily_mcp_search",
            new=MagicMock(return_value=None),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run_hotel_agent(self, tavily_result="Hotel results text"):
        from tripmate.agents.hotel import create_hotel_agent

        mock_integration = MagicMock()

        with patch("tripmate.agents.hotel.asyncio.run", return_value=tavily_result):
            agent = create_hotel_agent(mock_integration)
            return agent({"user_query": "hotels in Paris", "llm_calls": 0})

    def test_evidence_key_present(self):
        result = self._run_hotel_agent()
        self.assertIn("evidence", result)
        self.assertIn("hotel", result["evidence"])

    def test_source_is_tavily_mcp(self):
        result = self._run_hotel_agent()
        self.assertEqual(result["evidence"]["hotel"]["source"], "tavily_mcp")

    def test_is_verified_is_false(self):
        """Tavily web results are NOT independently verified by TripMate."""
        result = self._run_hotel_agent()
        self.assertFalse(result["evidence"]["hotel"]["is_verified"])

    def test_is_estimate_is_false(self):
        """Web search results are web-sourced, not LLM-generated estimates."""
        result = self._run_hotel_agent()
        self.assertFalse(result["evidence"]["hotel"]["is_estimate"])

    def test_content_is_string(self):
        result = self._run_hotel_agent()
        self.assertIsInstance(result["evidence"]["hotel"]["content"], str)

    def test_url_extracted_when_present_in_result(self):
        result = self._run_hotel_agent(
            tavily_result="Best hotels https://hotels.example.com/paris listed here"
        )
        source_url = result["evidence"]["hotel"].get("source_url", "")
        self.assertIn("http", source_url)

    def test_url_empty_when_no_url_in_result(self):
        result = self._run_hotel_agent(tavily_result="Hotel Ritz, Hotel Meurice")
        self.assertEqual(result["evidence"]["hotel"].get("source_url", ""), "")

    def test_evidence_is_json_serialisable(self):
        result = self._run_hotel_agent()
        json.dumps(result["evidence"])

    def test_retrieved_at_is_iso8601_string(self):
        result = self._run_hotel_agent()
        ts = result["evidence"]["hotel"]["retrieved_at"]
        datetime.fromisoformat(ts)

    def test_provider_failure_is_reported_as_unavailable_evidence(self):
        from tripmate.agents.hotel import create_hotel_agent
        from tripmate.core.exceptions import ExternalServiceError

        with patch(
            "tripmate.agents.hotel.asyncio.run",
            side_effect=ExternalServiceError(),
        ):
            result = create_hotel_agent(MagicMock())({"user_query": "hotels in Paris"})

        evidence = result["evidence"]["hotel"]
        self.assertEqual(evidence["source"], "unavailable")
        self.assertIn("unavailable", evidence["content"].lower())


# ---------------------------------------------------------------------------
# 6. Weather agent evidence
# ---------------------------------------------------------------------------

class WeatherAgentEvidenceTests(unittest.TestCase):
    def setUp(self):
        self._mcp_patchers = [
            patch(
                "tripmate.agents.weather.weather_mcp_search",
                new=MagicMock(return_value=None),
            ),
            patch(
                "tripmate.agents.weather.forecast_mcp_search",
                new=MagicMock(return_value=None),
            ),
        ]
        for patcher in self._mcp_patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _run_weather_agent(
        self,
        current="16°C sunny",
        forecast="18°C tomorrow",
        destination="London",
    ):
        from tripmate.agents.weather import create_weather_agent

        mock_llm = MagicMock()
        mock_integration = MagicMock()

        with patch(
            "tripmate.agents.weather.extract_destination", return_value=destination
        ), patch(
            "tripmate.agents.weather.asyncio.run",
            side_effect=[current, forecast],
        ):
            agent = create_weather_agent(mock_llm, mock_integration)
            return agent({"user_query": f"weather in {destination}"})

    def test_evidence_key_present(self):
        result = self._run_weather_agent()
        self.assertIn("evidence", result)
        self.assertIn("weather", result["evidence"])

    def test_source_is_openweather_mcp(self):
        result = self._run_weather_agent()
        self.assertEqual(result["evidence"]["weather"]["source"], "openweather_mcp")

    def test_is_verified_is_true(self):
        result = self._run_weather_agent()
        self.assertTrue(result["evidence"]["weather"]["is_verified"])

    def test_is_estimate_is_false(self):
        result = self._run_weather_agent()
        self.assertFalse(result["evidence"]["weather"]["is_estimate"])

    def test_records_contain_current_and_forecast(self):
        result = self._run_weather_agent()
        records = result["evidence"]["weather"]["records"]
        types = {r["type"] for r in records}
        self.assertIn("current_weather", types)
        self.assertIn("forecast", types)

    def test_evidence_is_json_serialisable(self):
        result = self._run_weather_agent()
        json.dumps(result["evidence"])

    def test_retrieved_at_is_iso8601_string(self):
        result = self._run_weather_agent()
        ts = result["evidence"]["weather"]["retrieved_at"]
        datetime.fromisoformat(ts)

    def test_provider_failures_are_reported_without_verified_records(self):
        from tripmate.agents.weather import create_weather_agent
        from tripmate.core.exceptions import ExternalServiceError

        with patch(
            "tripmate.agents.weather.extract_destination", return_value="London"
        ), patch(
            "tripmate.agents.weather.asyncio.run",
            side_effect=ExternalServiceError(),
        ):
            result = create_weather_agent(MagicMock(), MagicMock())(
                {"user_query": "weather in London"}
            )

        evidence = result["evidence"]["weather"]
        self.assertEqual(evidence["source"], "unavailable")
        self.assertFalse(evidence["is_verified"])
        self.assertEqual(evidence["records"], [])


# ---------------------------------------------------------------------------
# 7. Budget agent evidence
# ---------------------------------------------------------------------------

class BudgetAgentEvidenceTests(unittest.TestCase):
    def _run_budget_agent(self, llm_content="Budget: £1200 estimated"):
        from tripmate.agents.budget import create_budget_agent

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = llm_content

        with patch("tripmate.agents.budget.invoke_llm", return_value=mock_response):
            agent = create_budget_agent(mock_llm)
            return agent({
                "user_query": "budget for Paris trip",
                "trip_constraints": {},
                "flight_results": "",
                "hotel_results": "",
                "weather_results": "",
                "llm_calls": 0,
            })

    def test_evidence_key_present(self):
        result = self._run_budget_agent()
        self.assertIn("evidence", result)
        self.assertIn("budget", result["evidence"])

    def test_source_is_llm(self):
        result = self._run_budget_agent()
        self.assertEqual(result["evidence"]["budget"]["source"], "llm")

    def test_is_estimate_is_true(self):
        result = self._run_budget_agent()
        self.assertTrue(result["evidence"]["budget"]["is_estimate"])

    def test_is_verified_is_false(self):
        result = self._run_budget_agent()
        self.assertFalse(result["evidence"]["budget"]["is_verified"])

    def test_content_matches_budget_results(self):
        result = self._run_budget_agent("£1200 estimate")
        self.assertEqual(result["evidence"]["budget"]["content"], result["budget_results"])

    def test_evidence_is_json_serialisable(self):
        result = self._run_budget_agent()
        json.dumps(result["evidence"])

    def test_retrieved_at_is_iso8601_string(self):
        result = self._run_budget_agent()
        ts = result["evidence"]["budget"]["retrieved_at"]
        datetime.fromisoformat(ts)


# ---------------------------------------------------------------------------
# 8. _evidence_summary helper
# ---------------------------------------------------------------------------

class ItineraryEvidenceSummaryTests(unittest.TestCase):
    def test_empty_evidence_returns_fallback_message(self):
        summary = _evidence_summary({})
        self.assertIn("No structured evidence", summary)

    def test_llm_source_flagged_as_estimate_and_not_verified(self):
        evidence = {"budget": make_evidence(
            source="llm", content="est", is_estimate=True, is_verified=False
        )}
        summary = _evidence_summary(evidence)
        self.assertIn("ESTIMATE", summary)
        self.assertIn("NOT VERIFIED", summary)

    def test_openweather_shows_no_estimate_flag(self):
        evidence = {"weather": make_evidence(
            source="openweather_mcp", content="16C", is_estimate=False, is_verified=True
        )}
        summary = _evidence_summary(evidence)
        self.assertNotIn("ESTIMATE", summary)
        self.assertNotIn("NOT VERIFIED", summary)

    def test_tavily_flagged_as_not_verified(self):
        evidence = {"hotel": make_evidence(
            source="tavily_mcp", content="hotels", is_estimate=False, is_verified=False
        )}
        summary = _evidence_summary(evidence)
        self.assertIn("NOT VERIFIED", summary)

    def test_all_four_agents_appear_in_summary(self):
        evidence: AgentEvidence = {}
        for key, src, est, ver in [
            ("flight", "aviationstack_mcp+llm", True, True),
            ("hotel", "tavily_mcp", False, False),
            ("weather", "openweather_mcp", False, True),
            ("budget", "llm", True, False),
        ]:
            evidence = merge_evidence(
                evidence,
                {key: make_evidence(source=src, content="x", is_estimate=est, is_verified=ver)},
            )
        summary = _evidence_summary(evidence)
        for key in ("flight", "hotel", "weather", "budget"):
            self.assertIn(key, summary, f"{key} not in evidence summary")

    def test_retrieved_at_present_in_summary(self):
        evidence = {"budget": _minimal_evidence("llm")}
        summary = _evidence_summary(evidence)
        self.assertIn("retrieved", summary)


# ---------------------------------------------------------------------------
# 9. serialize_result — evidence key
# ---------------------------------------------------------------------------

class SerialiseResultTests(unittest.TestCase):
    def test_evidence_key_present_in_output(self):
        result = _minimal_result()
        serialised = serialize_result(result, "thread-x")
        self.assertIn("evidence", serialised)

    def test_evidence_defaults_to_empty_dict_when_absent(self):
        result = _minimal_result()
        serialised = serialize_result(result, "thread-x")
        self.assertEqual(serialised["evidence"], {})

    def test_evidence_passed_through_when_present(self):
        ev: AgentEvidence = {"budget": _minimal_evidence("llm")}
        result = _minimal_result(evidence=ev)
        serialised = serialize_result(result, "thread-x")
        self.assertIn("budget", serialised["evidence"])

    def test_existing_keys_still_present(self):
        """Backward compatibility: no existing keys removed."""
        result = _minimal_result()
        serialised = serialize_result(result, "thread-x")
        for key in (
            "thread_id", "answer", "flight_results", "hotel_results",
            "weather_results", "budget_results", "itinerary",
            "selected_agents", "trip_constraints", "supervisor_reasoning",
            "guardrail_allowed", "guardrail_reason", "approved",
            "human_feedback", "llm_calls",
        ):
            self.assertIn(key, serialised, f"Existing key missing: {key}")

    def test_evidence_is_json_serialisable(self):
        ev: AgentEvidence = {
            "flight": make_evidence(
                source="aviationstack_mcp+llm",
                content="fly info",
                is_estimate=True,
                is_verified=True,
                records=[{"type": "airports", "raw": "LHR"}],
            ),
            "hotel": make_evidence(
                source="tavily_mcp",
                content="hotel list",
                is_estimate=False,
                is_verified=False,
                source_url="https://example.com",
            ),
        }
        result = _minimal_result(evidence=ev)
        serialised = serialize_result(result, "thread-y")
        # Must not raise
        json.dumps(serialised)


# ---------------------------------------------------------------------------
# 10. Checkpoint / JSONB compatibility
# ---------------------------------------------------------------------------

class CheckpointCompatTests(unittest.TestCase):
    """Verify that all evidence values are JSON primitives (JSONB-safe)."""

    def _full_evidence(self) -> AgentEvidence:
        ev: AgentEvidence = {}
        items = [
            ("flight", make_evidence(
                source="aviationstack_mcp+llm",
                content="flights",
                is_estimate=True,
                is_verified=True,
                records=[{"type": "airports", "raw": "LHR"}, {"type": "airlines", "raw": "BA"}],
            )),
            ("hotel", make_evidence(
                source="tavily_mcp",
                content="hotels",
                is_estimate=False,
                is_verified=False,
                source_url="https://hotels.example.com",
            )),
            ("weather", make_evidence(
                source="openweather_mcp",
                content="16C",
                is_estimate=False,
                is_verified=True,
                records=[{"type": "current_weather", "raw": "16C"}, {"type": "forecast", "raw": "17C"}],
            )),
            ("budget", make_evidence(
                source="llm",
                content="£1200 estimate",
                is_estimate=True,
                is_verified=False,
            )),
        ]
        for key, item in items:
            ev = merge_evidence(ev, {key: item})
        return ev

    def test_full_evidence_is_json_serialisable(self):
        ev = self._full_evidence()
        serialised = json.dumps(ev)
        self.assertIsInstance(serialised, str)

    def test_round_trip_preserves_all_fields(self):
        ev = self._full_evidence()
        restored = json.loads(json.dumps(ev))
        for key in ("flight", "hotel", "weather", "budget"):
            self.assertIn(key, restored)

    def test_no_datetime_objects_in_evidence(self):
        """Datetime objects break the LangGraph JSONB codec — none must exist."""
        ev = self._full_evidence()

        def _check_no_datetime(obj: Any, path: str = "root") -> None:
            if isinstance(obj, datetime):
                self.fail(f"datetime object found at {path}")
            elif isinstance(obj, dict):
                for k, v in obj.items():
                    _check_no_datetime(v, f"{path}.{k}")
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    _check_no_datetime(item, f"{path}[{i}]")

        _check_no_datetime(ev)

    def test_retrieved_at_parses_as_iso8601_after_json_round_trip(self):
        ev = self._full_evidence()
        restored: dict = json.loads(json.dumps(ev))
        for agent_key in ("flight", "hotel", "weather", "budget"):
            ts = restored[agent_key]["retrieved_at"]
            datetime.fromisoformat(ts)  # must not raise

    def test_all_leaf_values_are_json_primitives(self):
        """Recursively check every leaf value is a JSON primitive type."""
        ev = self._full_evidence()
        allowed = (str, bool, int, float, type(None))

        def _check(obj: Any, path: str = "root") -> None:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    _check(v, f"{path}.{k}")
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    _check(item, f"{path}[{i}]")
            else:
                self.assertIsInstance(
                    obj,
                    allowed,
                    f"Non-primitive value at {path}: {type(obj).__name__}={obj!r}",
                )

        _check(ev)


# ---------------------------------------------------------------------------
# 11. Backward compatibility
# ---------------------------------------------------------------------------

class BackwardCompatTests(unittest.TestCase):
    def test_travel_state_original_fields_preserved(self):
        """All original TravelState fields must still be importable and intact."""
        from tripmate.graph.state import TravelState
        import typing

        hints = typing.get_type_hints(TravelState, include_extras=True)
        for field in (
            "messages", "user_query", "guardrail_allowed", "guardrail_reason",
            "selected_agents", "trip_constraints", "supervisor_reasoning",
            "flight_results", "hotel_results", "weather_results",
            "itinerary", "budget_results", "approval_request", "approved",
            "human_feedback", "final_response", "llm_calls",
        ):
            self.assertIn(field, hints, f"Original field missing from TravelState: {field}")

    def test_evidence_field_present_in_travel_state(self):
        from tripmate.graph.state import TravelState
        import typing

        hints = typing.get_type_hints(TravelState, include_extras=True)
        self.assertIn("evidence", hints)

    def test_serialize_result_with_no_evidence_key_in_source(self):
        """If the graph result dict has no 'evidence' key, output defaults to {}."""
        result = _minimal_result()
        result.pop("evidence", None)  # ensure absent
        serialised = serialize_result(result, "thread-z")
        self.assertEqual(serialised["evidence"], {})

    def test_existing_regression_test_result_shape_unchanged(self):
        """Simulate the shape asserted by existing test_regressions.py tests."""
        graph = MagicMock()
        graph.invoke.return_value = {"final_response": "ready"}
        from tripmate.services.travel_service import TravelService
        result = TravelService(graph).start("plan a trip")
        # Existing assertions from test_regressions.py
        self.assertTrue(result["thread_id"].startswith("user_"))
        self.assertEqual(result["answer"], "ready")
        # New: evidence key must also be present and not break anything
        self.assertIn("evidence", result)
        self.assertIsInstance(result["evidence"], dict)


if __name__ == "__main__":
    unittest.main()
