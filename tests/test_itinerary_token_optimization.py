"""Focused regressions for itinerary prompt compaction and token limits."""

import json
import unittest
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage

from tripmate.agents.itinerary import (
    ITINERARY_COMBINED_TOKEN_BUDGET,
    ITINERARY_INPUT_TOKEN_BUDGET,
    ITINERARY_MAX_COMPLETION_TOKENS,
    ITINERARY_TPM_SAFETY_MARGIN,
    ITINERARY_TPM_TOKEN_TARGET,
    _count_message_tokens,
    _count_text_tokens,
    _prompt_messages,
    create_itinerary_agent,
)
from tripmate.core.exceptions import ItineraryPromptTooLargeError
from tripmate.core.exceptions import ItineraryGenerationError
from tripmate.services.travel_service import serialize_result


def _state(**overrides):
    state = {
        "user_query": "Plan our anniversary trip to Kyoto; we need step-free access and vegetarian meals.",
        "trip_constraints": {
            "destination": "Kyoto",
            "dates": "2026-11-10 to 2026-11-15",
            "travelers": 2,
            "mobility": "step-free access",
            "diet": "vegetarian",
            "currency": "JPY",
        },
        "flight_results": "Flight arrives at KIX at 16:20; airport transfer by train.",
        "hotel_results": "",
        "weather_results": "Cool and dry; forecast is uncertain.",
        "budget_results": "",
        "evidence": {},
        "llm_calls": 3,
    }
    state.update(overrides)
    return state


def _valid_draft(extra=""):
    return (
        "# Trip Summary\nKyoto anniversary trip.\n\n"
        "## Day-by-Day Itinerary\nDay 1: Arrive at KIX.\n\n"
        "## Practical Notes\nConfirm step-free access.\n\n"
        "## Estimated Budget\nUse supplied estimates only.\n\n"
        "## Data Sources & Disclaimers\n"
        "Flight prices and real-time seat availability are AI-estimated and have NOT "
        "been verified against live booking systems; verify directly before purchasing.\n"
        + extra
    )


class ItineraryTokenOptimizationTests(unittest.TestCase):
    def test_full_query_and_all_constraints_are_in_prompt(self):
        query = "Keep the entire exact user request: " + "special phrase " * 40
        messages = _prompt_messages(_state(user_query=query))
        prompt = messages[1].content
        self.assertIn(query, prompt)
        for value in ("Kyoto", "2026-11-10 to 2026-11-15", "step-free access", "vegetarian", "JPY"):
            self.assertIn(value, prompt)

    def test_hotel_results_compact_json_without_losing_facts_or_sources(self):
        results = {
            "results": [
                {
                    "title": "Maple House",
                    "url": "https://example.test/maple",
                    "content": "Accessible room: JPY 18,500 per night; near Kyoto Station; Nov 10-15.",
                    "transport": "2-minute walk to station",
                }
            ],
            "response_time": 1.5,
        }
        evidence = {
            "hotel": {
                "source": "tavily_mcp",
                "content": json.dumps(results, indent=2),
                "source_url": "https://example.test/maple",
                "retrieved_at": "2026-11-03T00:00:00+00:00",
                "is_estimate": False,
                "is_verified": False,
            }
        }
        prompt = _prompt_messages(_state(hotel_results=json.dumps(results, indent=2), evidence=evidence))[1].content
        self.assertIn("Maple House", prompt)
        self.assertIn("18,500", prompt)
        self.assertIn("JPY", prompt)
        self.assertIn("Kyoto Station", prompt)
        self.assertIn("Nov 10-15", prompt)
        self.assertIn("https://example.test/maple", prompt)
        self.assertEqual(prompt.count("https://example.test/maple"), 1)
        self.assertIn("NOT VERIFIED", prompt)
        self.assertNotIn("\n      \"title\"", prompt)

    def test_nested_json_result_is_compacted_without_losing_fields(self):
        wrapped = json.dumps([{
            "type": "text",
            "text": json.dumps({
                "results": [{"title": "Maple House", "url": "https://example.test/maple", "content": "Step-free entrance; JPY 18,500 per night."}],
                "request_id": "search-123",
            }, indent=2),
        }], indent=2)
        prompt = _prompt_messages(_state(hotel_results=wrapped))[1].content
        self.assertIn("Maple House", prompt)
        self.assertIn("Step-free entrance", prompt)
        self.assertIn("JPY 18,500 per night", prompt)
        self.assertIn("search-123", prompt)
        self.assertNotIn("\\n  \\\"results\\\"", prompt)

    def test_budget_result_whitespace_compaction_preserves_totals_and_caveats(self):
        budget = """Estimated total: JPY 120,000

        Lodging: JPY 74,000 (5 nights)
        Transport: JPY 16,000
        Meals: JPY 30,000
        Warning: prices are estimates and may change."""
        prompt = _prompt_messages(_state(budget_results=budget))[1].content
        for fact in ("JPY 120,000", "JPY 74,000", "5 nights", "JPY 16,000", "JPY 30,000", "prices are estimates"):
            self.assertIn(fact, prompt)
        self.assertNotIn("\n\n        Lodging", prompt)

    def test_unique_evidence_source_details_and_records_are_included(self):
        evidence = {
            "hotel": {
                "source": "tavily_mcp",
                "content": "Hotel result",
                "source_url": "https://source.test/details",
                "retrieved_at": "2026-11-03T00:00:00+00:00",
                "is_estimate": False,
                "is_verified": False,
                "records": [{"warning": "Elevator access must be confirmed", "price": "JPY 18,500"}],
            }
        }
        prompt = _prompt_messages(_state(evidence=evidence))[1].content
        self.assertIn("https://source.test/details", prompt)
        self.assertIn("Elevator access must be confirmed", prompt)
        self.assertIn("2026-11-03T00:00:00+00:00", prompt)
        self.assertIn("NOT VERIFIED", prompt)

    def test_evidence_records_already_present_in_agent_output_are_not_duplicated(self):
        record = {"warning": "Elevator access must be confirmed", "price": "JPY 18,500"}
        prompt = _prompt_messages(
            _state(
                hotel_results="Elevator access must be confirmed; price JPY 18,500",
                evidence={
                    "hotel": {
                        "source": "tavily_mcp",
                        "is_estimate": False,
                        "is_verified": False,
                        "records": [record],
                    }
                },
            )
        )[1].content
        self.assertEqual(prompt.count("Elevator access must be confirmed"), 1)
        self.assertEqual(prompt.count("JPY 18,500"), 1)

    def test_persisted_agent_history_is_measured_but_not_added_to_prompt(self):
        old_human_turns = [HumanMessage(content=f"Old user requirement {index}") for index in range(8)]
        old_agent_outputs = [AIMessage(content="old final response " * 200) for _ in range(20)]
        state = _state(
            messages=[*old_human_turns, *old_agent_outputs, HumanMessage(content="Add Kyoto museums")],
            user_query="Add Kyoto museums",
            thread_id_hash="ab12cd34ef56ab78",
        )
        prompt = _prompt_messages(state)[1].content
        self.assertIn("User Query:\nAdd Kyoto museums", prompt)
        self.assertIn("Old user requirement 0", prompt)
        self.assertIn("Old user requirement 7", prompt)
        self.assertNotIn("old final response", prompt)

    def test_structured_research_metadata_is_compacted_before_budget_failure(self):
        llm = MagicMock()
        hotel_data = {
            "response_time": "metadata " * 8_000,
            "results": [{
                "title": "Maple House",
                "url": "https://example.test/maple",
                "content": "Step-free room near Kyoto Station; JPY 18,500 per night.",
                "score": 0.91,
            }],
        }
        state = _state(hotel_results=json.dumps(hotel_data))
        with patch("tripmate.agents.itinerary.invoke_llm", return_value=AIMessage(content=_valid_draft())) as invoke:
            create_itinerary_agent(llm)(state)
        prompt = invoke.call_args.args[1][1].content
        self.assertIn("Maple House", prompt)
        self.assertIn("Step-free room", prompt)
        self.assertIn("JPY 18,500", prompt)
        self.assertNotIn("metadata", prompt)

    def test_message_token_count_includes_formatting_overhead(self):
        messages = _prompt_messages(_state())
        count = _count_message_tokens(messages, "openai/gpt-oss-120b")
        self.assertGreater(count, 0)
        self.assertEqual(count, _count_message_tokens(messages, "openai/gpt-oss-120b"))

    def test_reconstructed_6331_token_prompt_is_accepted(self):
        llm = MagicMock()
        query = "Plan " + " constraint " * 2_745 + " 1"
        state = _state(user_query=query)
        messages = _prompt_messages(state)
        self.assertLessEqual(_count_message_tokens(messages), ITINERARY_INPUT_TOKEN_BUDGET)
        with patch("tripmate.agents.itinerary.invoke_llm", return_value=AIMessage(content=_valid_draft())) as invoke:
            result = create_itinerary_agent(llm)(state)
        self.assertIn("step-free access", invoke.call_args.args[1][1].content)
        self.assertEqual(result["itinerary"], _valid_draft())

    def test_configured_combined_budget_stays_below_tpm_target_with_margin(self):
        self.assertEqual(ITINERARY_INPUT_TOKEN_BUDGET, 5_900)
        self.assertEqual(ITINERARY_MAX_COMPLETION_TOKENS, 1_536)
        self.assertEqual(ITINERARY_COMBINED_TOKEN_BUDGET, 7_436)
        self.assertLessEqual(
            ITINERARY_COMBINED_TOKEN_BUDGET,
            ITINERARY_TPM_TOKEN_TARGET - ITINERARY_TPM_SAFETY_MARGIN,
        )

    def test_prompt_budget_enforced_and_completion_cap_bound(self):
        llm = MagicMock()
        bounded_llm = MagicMock()
        llm.bind.return_value = bounded_llm
        with patch("tripmate.agents.itinerary.invoke_llm", return_value=AIMessage(content=_valid_draft())) as invoke:
            result = create_itinerary_agent(llm)(_state())
        args, kwargs = llm.bind.call_args
        self.assertEqual(args, ())
        self.assertEqual(kwargs, {"max_completion_tokens": ITINERARY_MAX_COMPLETION_TOKENS})
        messages = invoke.call_args.args[1]
        self.assertLessEqual(_count_message_tokens(messages, "openai/gpt-oss-120b"), ITINERARY_INPUT_TOKEN_BUDGET)
        self.assertEqual(result["itinerary"], _valid_draft())
        self.assertEqual(result["llm_calls"], 4)

    def test_prompt_budget_diagnostics_include_counts_without_prompt_content(self):
        llm = MagicMock()
        state = _state(
            user_query="Sensitive destination request marker",
            thread_id_hash="ab12cd34ef56ab78",
        )
        with (
            patch("tripmate.agents.itinerary.invoke_llm", return_value=AIMessage(content=_valid_draft())),
            self.assertLogs("tripmate.agents.itinerary", level="INFO") as captured,
        ):
            create_itinerary_agent(llm)(state)
        record = captured.records[0]
        self.assertEqual(record.event, "itinerary.prompt.budget")
        self.assertEqual(record.current_request_tokens, _count_text_tokens(state["user_query"]))
        self.assertEqual(record.input_budget_tokens, ITINERARY_INPUT_TOKEN_BUDGET)
        self.assertEqual(record.thread_id_hash, "ab12cd34ef56ab78")
        self.assertGreaterEqual(record.remaining_available_tokens, 0)
        self.assertNotIn("Sensitive destination request marker", captured.output)

    def test_oversized_mandatory_input_fails_before_provider_call(self):
        llm = MagicMock()
        huge_state = _state(user_query="Complete request " + "important constraint " * 12_000)
        with patch("tripmate.agents.itinerary.invoke_llm") as invoke:
            with self.assertRaises(ItineraryPromptTooLargeError):
                create_itinerary_agent(llm)(huge_state)
        invoke.assert_not_called()
        llm.bind.assert_not_called()

    def test_prompt_still_over_budget_after_safe_compaction_fails_closed(self):
        llm = MagicMock()
        huge_state = _state(
            user_query="Keep every requirement: " + "must preserve this requirement " * 12_000,
            hotel_results=json.dumps({"response_time": "discardable metadata " * 4_000}),
        )
        with patch("tripmate.agents.itinerary.invoke_llm") as invoke:
            with self.assertRaises(ItineraryPromptTooLargeError):
                create_itinerary_agent(llm)(huge_state)
        invoke.assert_not_called()
        llm.bind.assert_not_called()

    def test_complete_itinerary_response_is_serialized_unchanged(self):
        full_itinerary = _valid_draft("\n" + ("Detailed activity and transit directions.\n" * 100))
        output_tokens = _count_message_tokens([AIMessage(content=full_itinerary)])
        self.assertLessEqual(output_tokens, ITINERARY_MAX_COMPLETION_TOKENS)
        llm = MagicMock()
        with patch("tripmate.agents.itinerary.invoke_llm", return_value=AIMessage(content=full_itinerary)):
            result = create_itinerary_agent(llm)(_state())
        self.assertEqual(result["itinerary"], full_itinerary)
        serialized = serialize_result({**_state(), **result}, "thread-test")
        self.assertEqual(serialized["itinerary"], full_itinerary)
        json.dumps(serialized)

    def test_output_limit_response_is_not_sent_for_human_approval(self):
        llm = MagicMock()
        truncated = AIMessage(
            content=_valid_draft(),
            response_metadata={"finish_reason": "length"},
        )
        with patch("tripmate.agents.itinerary.invoke_llm", return_value=truncated):
            with self.assertRaises(ItineraryGenerationError):
                create_itinerary_agent(llm)(_state())

    def test_truncated_response_retries_once_with_condensed_prompt(self):
        llm = MagicMock()
        truncated = AIMessage(content="partial", response_metadata={"finish_reason": "length"})
        complete = AIMessage(content=_valid_draft())
        with patch("tripmate.agents.itinerary.invoke_llm", side_effect=[truncated, complete]) as invoke:
            result = create_itinerary_agent(llm)(_state())
        self.assertEqual(result["itinerary"], _valid_draft())
        self.assertEqual(invoke.call_count, 2)
        self.assertIn("Compact retry", invoke.call_args_list[1].args[1][1].content)

    def test_retry_exhaustion_is_sanitized_and_never_approvable(self):
        llm = MagicMock()
        truncated = AIMessage(content="partial", response_metadata={"finish_reason": "length"})
        with patch("tripmate.agents.itinerary.invoke_llm", side_effect=[truncated, truncated]) as invoke:
            with self.assertRaises(ItineraryGenerationError) as raised:
                create_itinerary_agent(llm)(_state())
        self.assertEqual(invoke.call_count, 2)
        self.assertEqual(raised.exception.status_code, 502)
        self.assertNotIn("partial", raised.exception.public_message)

    def test_invalid_incomplete_draft_is_retried_then_rejected(self):
        llm = MagicMock()
        invalid = AIMessage(content="# Trip Summary\nNo day-by-day plan.")
        with patch("tripmate.agents.itinerary.invoke_llm", side_effect=[invalid, invalid]) as invoke:
            with self.assertRaises(ItineraryGenerationError):
                create_itinerary_agent(llm)(_state())
        self.assertEqual(invoke.call_count, 2)

    def test_prompt_preserves_evidence_caveats_and_flight_disclaimer(self):
        evidence = {"flight": {"source": "llm", "is_estimate": True, "is_verified": False}}
        prompt = _prompt_messages(_state(evidence=evidence))[1].content
        self.assertIn("ESTIMATE", prompt)
        self.assertIn("NOT VERIFIED", prompt)
        self.assertIn("Flight prices and real-time seat availability", prompt)


if __name__ == "__main__":
    unittest.main()
