"""Application-level travel workflow operations."""

import uuid
from time import perf_counter
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from tripmate.agents.shared import empty_constraints
from tripmate.core.exceptions import BadRequestError
from tripmate.core.logging import get_correlation_id, get_logger, hash_thread_id
from tripmate.core.observability import LLMInvocationTracker

logger = get_logger(__name__)


def _interrupt_payload(
    result: dict[str, Any],
) -> dict[str, Any] | None:
    interrupts = result.get("__interrupt__", [])
    if not interrupts:
        return None

    first_interrupt = interrupts[0]
    payload = getattr(first_interrupt, "value", first_interrupt)
    return payload if isinstance(payload, dict) else {"value": payload}


def serialize_result(
    result: dict[str, Any],
    thread_id: str,
) -> dict[str, Any]:
    messages = result.get("messages", [])
    last_message = messages[-1].content if messages else ""
    answer = result.get("final_response") or last_message
    interrupt_payload = _interrupt_payload(result)

    if interrupt_payload:
        answer = interrupt_payload.get("draft_itinerary") or result.get(
            "itinerary", ""
        )

    return {
        "thread_id": thread_id,
        "answer": answer,
        "requires_approval": interrupt_payload is not None,
        "approval_request": (
            interrupt_payload.get("approval_request", "")
            if interrupt_payload
            else result.get("approval_request", "")
        ),
        "flight_results": result.get("flight_results", ""),
        "hotel_results": result.get("hotel_results", ""),
        "weather_results": result.get("weather_results", ""),
        "budget_results": result.get("budget_results", ""),
        "itinerary": (
            interrupt_payload.get("draft_itinerary", "")
            if interrupt_payload
            else result.get("itinerary", "")
        ),
        "selected_agents": result.get("selected_agents", []),
        "trip_constraints": result.get("trip_constraints", {}),
        "supervisor_reasoning": result.get("supervisor_reasoning", ""),
        "guardrail_allowed": result.get("guardrail_allowed", True),
        "guardrail_reason": result.get("guardrail_reason", ""),
        "approved": result.get("approved"),
        "human_feedback": result.get("human_feedback", ""),
        "llm_calls": result.get("llm_calls", 0),
    }


class TravelService:
    def __init__(self, travel_graph: Any) -> None:
        self._travel_graph = travel_graph

    def start(
        self,
        user_input: str,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        if not thread_id:
            thread_id = f"user_{uuid.uuid4().hex}"

        invocation = {
            "messages": [HumanMessage(content=user_input)],
            "user_query": user_input,
            "guardrail_allowed": True,
            "guardrail_reason": "",
            "selected_agents": [],
            "trip_constraints": empty_constraints(),
            "supervisor_reasoning": "",
            "flight_results": "",
            "hotel_results": "",
            "weather_results": "",
            "budget_results": "",
            "itinerary": "",
            "approval_request": "",
            "approved": False,
            "human_feedback": "",
            "final_response": "",
            "llm_calls": 0,
        }
        return self._invoke_workflow(
            invocation,
            thread_id,
            initial_llm_calls=0,
        )

    def resume(
        self,
        thread_id: str,
        approved: bool,
        feedback: str = "",
    ) -> dict[str, Any]:
        if not thread_id:
            raise BadRequestError(
                "thread_id is required to resume a travel plan."
            )

        config = {"configurable": {"thread_id": thread_id}}
        snapshot = self._travel_graph.get_state(config)
        initial_llm_calls = snapshot.values.get("llm_calls", 0)
        return self._invoke_workflow(
            Command(
                resume={
                    "approved": approved,
                    "feedback": feedback.strip(),
                }
            ),
            thread_id,
            initial_llm_calls=initial_llm_calls,
        )

    def _invoke_workflow(
        self,
        invocation: Any,
        thread_id: str,
        initial_llm_calls: int,
    ) -> dict[str, Any]:
        correlation_id = get_correlation_id()
        tracker = LLMInvocationTracker(count=initial_llm_calls)
        configurable: dict[str, Any] = {
            "thread_id": thread_id,
            "tripmate_llm_tracker": tracker,
        }
        metadata = (
            {"correlation_id": correlation_id}
            if correlation_id
            else {}
        )
        config = {
            "configurable": configurable,
            "metadata": metadata,
        }
        thread_id_hash = hash_thread_id(thread_id)
        started_at = perf_counter()
        try:
            result = self._travel_graph.invoke(invocation, config=config)
        except Exception as exception:
            logger.error(
                "Travel workflow failed.",
                extra={
                    "event": "workflow.failed",
                    "thread_id_hash": thread_id_hash,
                    "duration_ms": round(
                        (perf_counter() - started_at) * 1000,
                        2,
                    ),
                    "llm_calls": tracker.snapshot(),
                    "error_type": type(exception).__name__,
                    "outcome": "failure",
                },
            )
            raise

        result["llm_calls"] = tracker.snapshot()
        logger.info(
            "Travel workflow completed.",
            extra={
                "event": "workflow.completed",
                "thread_id_hash": thread_id_hash,
                "duration_ms": round(
                    (perf_counter() - started_at) * 1000,
                    2,
                ),
                "llm_calls": tracker.snapshot(),
                "outcome": "success",
            },
        )
        return serialize_result(result, thread_id)
