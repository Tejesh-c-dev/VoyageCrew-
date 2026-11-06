"""Draft itinerary generation agent."""

from __future__ import annotations

import ast
import json
import logging
import re
from typing import Any

import tiktoken
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from tripmate.agents.shared import AgentNode
from tripmate.core.observability import invoke_llm
from tripmate.core.exceptions import (
    ItineraryGenerationError,
    ItineraryPromptTooLargeError,
)
from tripmate.graph.state import TravelState

ITINERARY_INPUT_TOKEN_BUDGET = 5_900
ITINERARY_MAX_COMPLETION_TOKENS = 1_536
ITINERARY_MAX_ATTEMPTS = 2
ITINERARY_TPM_TOKEN_TARGET = 8_000
ITINERARY_TPM_SAFETY_MARGIN = 500
ITINERARY_REASONING_EFFORT = "low"
ITINERARY_RETRY_REASONING_EFFORT = "none"
ITINERARY_RESERVE_TOKENS = (
    ITINERARY_TPM_TOKEN_TARGET - ITINERARY_INPUT_TOKEN_BUDGET
)
ITINERARY_COMBINED_TOKEN_BUDGET = (
    ITINERARY_INPUT_TOKEN_BUDGET + ITINERARY_MAX_COMPLETION_TOKENS
)
_FALLBACK_ENCODING = "o200k_base"
logger = logging.getLogger(__name__)


def _evidence_summary(
    evidence: dict,
    represented_results: dict[str, str] | None = None,
) -> str:
    """Build concise provenance notes for inclusion in prompts."""
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
        result_text = (represented_results or {}).get(f"{key}_results", "")
        source = item.get("source", "unknown")
        label = label_map.get(source, source)
        flags = []
        if item.get("is_estimate", False):
            flags.append("ESTIMATE")
        if not item.get("is_verified", False):
            flags.append("NOT VERIFIED")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        retrieved = item.get("retrieved_at", "unknown time")
        lines.append(f"- {key}: {label}{flag_str} (retrieved: {retrieved})")
        source_url = item.get("source_url")
        if source_url and source_url not in result_text:
            lines.append(f"  Source: {source_url}")
        records = item.get("records") or []
        unique_records = []
        for record in records:
            leaves = _record_leaf_values(record)
            missing = [value for value in leaves if value not in result_text]
            if missing:
                unique_records.append(record)
        if unique_records:
            lines.append(f"  Records: {_compact_value(unique_records)}")
    return "\n".join(lines)


def _record_leaf_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [
            leaf
            for nested in value.values()
            for leaf in _record_leaf_values(nested)
        ]
    if isinstance(value, (list, tuple)):
        return [leaf for nested in value for leaf in _record_leaf_values(nested)]
    if value is None or isinstance(value, bool):
        return []
    return [_compact_value(value)]


def _compact_value(value: Any) -> str:
    """Serialize structured results compactly without dropping their fields."""
    def parse_structured_string(text: str) -> Any:
        stripped = text.strip()
        if stripped and stripped[:1] in "[{":
            for parser in (json.loads, ast.literal_eval):
                try:
                    parsed = parser(stripped)
                    if isinstance(parsed, (dict, list, tuple)):
                        return parsed
                except (ValueError, SyntaxError, TypeError):
                    pass
        return None

    if isinstance(value, str):
        parsed = parse_structured_string(value)
        if parsed is not None:
            value = parsed
        else:
            # Whitespace normalization preserves every word, number, URL, and
            # punctuation mark while removing formatting-only expansion.
            return re.sub(r"\s+", " ", value).strip()
    if isinstance(value, tuple):
        value = list(value)
    if isinstance(value, (dict, list)):
        def compact_nested(item: Any) -> Any:
            if isinstance(item, str):
                parsed = parse_structured_string(item)
                if parsed is not None:
                    return compact_nested(parsed)
                return re.sub(r"\s+", " ", item).strip()
            if isinstance(item, dict):
                return {key: compact_nested(nested) for key, nested in item.items()}
            if isinstance(item, (list, tuple)):
                return [compact_nested(nested) for nested in item]
            return item

        return json.dumps(
            compact_nested(value),
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
    return str(value)


def _output_limit_reached(response: Any) -> bool:
    """Recognize provider metadata that says the draft stopped at its limit."""
    return _finish_reason(response) in {
        "length",
        "max_tokens",
        "max_completion_tokens",
        "token_limit",
        "output_limit",
    }


def _history_context(
    state: TravelState,
    *,
    max_turns: int | None = None,
) -> tuple[str, int, int]:
    """Return prior user turns, excluding accumulated assistant/node outputs."""
    messages = state.get("messages", [])
    user_messages = [
        str(message.content)
        for message in messages
        if isinstance(message, HumanMessage)
        and isinstance(message.content, str)
        and message.content.strip()
    ]
    current_query = str(state.get("user_query", ""))
    if user_messages and user_messages[-1].strip() == current_query.strip():
        user_messages.pop()
    if max_turns is None:
        relevant = user_messages
    elif max_turns <= 0:
        relevant = []
    else:
        relevant = user_messages[-max_turns:]
    context = "\n\n".join(
        f"Prior request {index + 1}:\n{message}"
        for index, message in enumerate(relevant)
    )
    return context, len(user_messages), len(relevant)


_OMIT_RESEARCH_METADATA = {
    "response_time",
    "request_id",
    "query",
    "images",
    "favicon",
    "score",
    "rank",
    "position",
}


def _compact_research_value(value: Any) -> str:
    """Compact structured provider wrappers without dropping factual fields."""
    compacted = _compact_value(value)
    try:
        parsed = json.loads(compacted)
    except (TypeError, ValueError):
        return compacted

    def strip_metadata(item: Any) -> Any:
        if isinstance(item, dict):
            return {
                key: strip_metadata(nested)
                for key, nested in item.items()
                if key.casefold() not in _OMIT_RESEARCH_METADATA
            }
        if isinstance(item, list):
            result = []
            seen = set()
            for nested in item:
                cleaned = strip_metadata(nested)
                marker = json.dumps(cleaned, ensure_ascii=False, sort_keys=True, default=str)
                if marker not in seen:
                    seen.add(marker)
                    result.append(cleaned)
            return result
        if isinstance(item, str):
            try:
                nested = json.loads(item)
            except ValueError:
                return item
            return strip_metadata(nested)
        return item

    return _compact_value(strip_metadata(parsed))


def _prompt_messages(
    state: TravelState,
    *,
    concise: bool = False,
    compact_research: bool = False,
    history_turn_limit: int | None = None,
) -> list[Any]:
    evidence = state.get("evidence", {})
    history, _, _ = _history_context(state, max_turns=history_turn_limit)
    compact = _compact_research_value if compact_research else _compact_value
    result_fields = {
        f"{name}_results": compact(state.get(f"{name}_results", ""))
        for name in ("flight", "hotel", "weather", "budget")
    }
    prompt = f"""Create a complete travel itinerary.

Previous User Requests (relevant conversation context):
{history or 'No previous user requests.'}

User Query:
{state['user_query']}

Trip Constraints:
{compact(state.get('trip_constraints', {}))}

Flight Results:
{result_fields['flight_results']}

Hotel Results:
{result_fields['hotel_results']}

Weather Results:
{result_fields['weather_results']}

Budget Results:
{result_fields['budget_results']}

Evidence Summary (data provenance — use this to caveat estimates):
{_evidence_summary(evidence, result_fields)}

Create a concise, practical draft for human review. Use only supplied facts:
never invent attractions, prices, flights, weather, or citations. Mark estimates
and unverified data clearly. Keep flight facts limited to supplied results and
preserve this disclaimer when flight pricing or availability is mentioned:
"Flight prices and real-time seat availability are AI-estimated and have NOT
been verified against live booking systems; verify directly before purchasing."

Use these headings: Trip Summary; Day-by-Day Itinerary; Practical Notes;
Estimated Budget; Data Sources & Disclaimers. State when information is
unavailable instead of inventing it. Give each day 2–4 useful bullets and avoid
repeating source data already listed elsewhere."""
    if concise:
        prompt += """

Compact retry: use at most 2 bullets per day, short phrases, and no repeated
facts. Keep all required headings, evidence caveats, and the flight disclaimer."""
    return [
        SystemMessage(content="You are an expert travel planner."),
        HumanMessage(content=prompt),
    ]


def _token_encoder(model: str | None = None):
    try:
        return tiktoken.encoding_for_model(model) if model else tiktoken.get_encoding(_FALLBACK_ENCODING)
    except (KeyError, ValueError):
        return tiktoken.get_encoding(_FALLBACK_ENCODING)


def _count_text_tokens(value: str, model: str | None = None) -> int:
    return len(_token_encoder(model).encode(value))


def _count_message_tokens(messages: list[Any], model: str | None = None) -> int:
    """Count content plus explicit ChatML-style message framing overhead."""
    encoding = _token_encoder(model)
    # Groq's chat framing is not public tokenizer metadata. Three tokens per
    # message plus three priming tokens is a conservative, explicit allowance.
    return sum(
        3 + len(encoding.encode(message.type)) + len(encoding.encode(str(message.content)))
        for message in messages
    ) + 3


def _prompt_token_breakdown(
    state: TravelState,
    messages: list[Any],
    model: str | None,
    *,
    compact_research: bool,
    history_turn_limit: int | None = None,
) -> dict[str, int]:
    history, prior_user_turns, included_history_turns = _history_context(
        state,
        max_turns=history_turn_limit,
    )
    content = str(messages[1].content)
    query = str(state.get("user_query", ""))
    compact = _compact_research_value if compact_research else _compact_value
    constraints = compact(state.get("trip_constraints", {}))
    result_values = [
        compact(state.get(f"{name}_results", ""))
        for name in ("flight", "hotel", "weather", "budget")
    ]
    evidence = _evidence_summary(
        state.get("evidence", {}),
        {
            f"{name}_results": compact(state.get(f"{name}_results", ""))
            for name in ("flight", "hotel", "weather", "budget")
        },
    )
    instruction_text = content
    for component in (history, query, constraints, *result_values, evidence):
        if component:
            instruction_text = instruction_text.replace(component, "", 1)
    all_history = list(state.get("messages", []))
    current_removed = False
    history_tokens = 0
    for message in reversed(all_history):
        if (
            not current_removed
            and isinstance(message, HumanMessage)
            and str(message.content).strip() == query.strip()
        ):
            current_removed = True
            continue
        history_tokens += _count_text_tokens(str(message.content), model)
    return {
        "system_instruction_tokens": _count_text_tokens(
            str(messages[0].content) + instruction_text,
            model,
        ),
        "current_request_tokens": _count_text_tokens(query, model),
        "previous_history_tokens": history_tokens,
        "previous_user_request_count": prior_user_turns,
        "persisted_history_message_count": len(all_history) - int(current_removed),
        "included_history_tokens": _count_text_tokens(history, model),
        "included_history_turns": included_history_turns,
        "trip_constraint_tokens": _count_text_tokens(constraints, model),
        "retrieved_evidence_tokens": _count_text_tokens(evidence, model),
        "agent_output_tokens": sum(
            _count_text_tokens(value, model) for value in result_values
        ),
        "estimated_total_tokens": _count_message_tokens(messages, model),
    }


def _log_prompt_budget(
    state: TravelState,
    breakdown: dict[str, int],
    *,
    compacted: bool,
    initial_estimate: int,
) -> None:
    estimated_total = breakdown["estimated_total_tokens"]
    remaining = ITINERARY_INPUT_TOKEN_BUDGET - estimated_total
    extra = {
        "event": "itinerary.prompt.budget",
        **breakdown,
        "initial_estimated_tokens": initial_estimate,
        "input_budget_tokens": ITINERARY_INPUT_TOKEN_BUDGET,
        "total_reserve_tokens": ITINERARY_RESERVE_TOKENS,
        "completion_reserve_tokens": ITINERARY_MAX_COMPLETION_TOKENS,
        "safety_reserve_tokens": ITINERARY_TPM_SAFETY_MARGIN,
        "remaining_available_tokens": remaining,
        "thread_id_hash": state.get("thread_id_hash", "unknown"),
        "compaction_level": int(compacted),
        "outcome": "failure" if remaining < 0 else "success",
    }
    logger.info("Itinerary prompt token breakdown.", extra=extra)


def create_itinerary_agent(llm: Any) -> AgentNode:
    def generation_kwargs(
        model_name: Any,
        *,
        retry: bool,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "max_completion_tokens": ITINERARY_MAX_COMPLETION_TOKENS,
        }
        if isinstance(model_name, str) and "gpt-oss" in model_name.casefold():
            kwargs["reasoning_effort"] = (
                ITINERARY_RETRY_REASONING_EFFORT
                if retry
                else ITINERARY_REASONING_EFFORT
            )
        return kwargs

    def itinerary_agent(state: TravelState) -> dict[str, Any]:
        messages = _prompt_messages(state)
        model_name = getattr(llm, "model_name", None) or getattr(llm, "model", None)
        token_count = _count_message_tokens(messages, model_name)
        compacted = False
        history_limit = None
        if token_count > ITINERARY_INPUT_TOKEN_BUDGET:
            # Apply lossless formatting/metadata compaction first, then shed
            # oldest prior user turns until the prompt fits. The current query
            # and constraints are always rendered from the original state.
            candidate = _prompt_messages(state, compact_research=True)
            candidate_count = _count_message_tokens(candidate, model_name)
            if candidate_count < token_count:
                messages, token_count, compacted = candidate, candidate_count, True
            _, prior_turns, _ = _history_context(state)
            for keep_turns in range(max(prior_turns - 1, 0), -1, -1):
                if token_count <= ITINERARY_INPUT_TOKEN_BUDGET:
                    break
                candidate = _prompt_messages(
                    state,
                    compact_research=True,
                    history_turn_limit=keep_turns,
                )
                candidate_count = _count_message_tokens(candidate, model_name)
                if candidate_count < token_count:
                    messages, token_count = candidate, candidate_count
                    history_limit = keep_turns
                    compacted = True
        breakdown = _prompt_token_breakdown(
            state,
            messages,
            model_name,
            compact_research=compacted,
            history_turn_limit=history_limit,
        )
        _log_prompt_budget(
            state,
            breakdown,
            compacted=compacted,
            initial_estimate=_count_message_tokens(
                _prompt_messages(state),
                model_name,
            ),
        )
        if token_count > ITINERARY_INPUT_TOKEN_BUDGET or (
            token_count + ITINERARY_MAX_COMPLETION_TOKENS
            > ITINERARY_TPM_TOKEN_TARGET - ITINERARY_TPM_SAFETY_MARGIN
        ):
            raise ItineraryPromptTooLargeError()

        response = None
        for attempt in range(ITINERARY_MAX_ATTEMPTS):
            active_messages = messages if attempt == 0 else _prompt_messages(
                state,
                concise=True,
                compact_research=compacted,
                history_turn_limit=history_limit,
            )
            response = invoke_llm(
                llm.bind(
                    **generation_kwargs(
                        model_name,
                        retry=attempt > 0,
                    )
                ),
                active_messages,
            )
            if not _output_limit_reached(response) and _valid_itinerary(response):
                break
            usage = getattr(response, "usage_metadata", None) or {}
            metadata = getattr(response, "response_metadata", None) or {}
            provider_usage = metadata.get("token_usage") or metadata.get("usage") or {}
            logger.warning(
                "Itinerary generation was incomplete.",
                extra={
                    "event": "itinerary.generation.incomplete",
                    "attempt": attempt + 1,
                    "attempts": ITINERARY_MAX_ATTEMPTS,
                    "finish_reason": _finish_reason(response),
                    "input_tokens": usage.get("input_tokens", provider_usage.get("prompt_tokens")),
                    "output_tokens": usage.get("output_tokens", provider_usage.get("completion_tokens")),
                },
            )
        else:
            raise ItineraryGenerationError()
        approval_request = (
            "Please review the generated draft itinerary. Approve it to create "
            "the final polished plan, or provide feedback for revision."
        )
        return {
            "itinerary": response.content,
            "approval_request": approval_request,
            "messages": [AIMessage(content="Draft itinerary created for human review.")],
            "llm_calls": state.get("llm_calls", 0) + 1,
        }

    return itinerary_agent


def _finish_reason(response: Any) -> str:
    metadata = getattr(response, "response_metadata", None) or {}
    additional = getattr(response, "additional_kwargs", None) or {}
    reason = metadata.get("finish_reason") or additional.get("finish_reason")
    if isinstance(reason, dict):
        reason = reason.get("type") or reason.get("reason")
    return str(reason or "").strip().lower()


def _valid_itinerary(response: Any) -> bool:
    content = getattr(response, "content", None)
    if not isinstance(content, str) or not content.strip():
        return False
    normalized = content.casefold()
    return all(
        heading.casefold() in normalized
        for heading in (
            "Trip Summary",
            "Day-by-Day Itinerary",
            "Practical Notes",
            "Estimated Budget",
            "Data Sources & Disclaimers",
        )
    )
