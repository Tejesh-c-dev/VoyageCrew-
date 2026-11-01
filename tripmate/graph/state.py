"""LangGraph state schema for travel planning."""

import operator
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from typing_extensions import Required


class TravelState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], operator.add]
    user_query: Required[str]
    guardrail_allowed: bool
    guardrail_reason: str
    selected_agents: list[str]
    trip_constraints: dict[str, Any]
    supervisor_reasoning: str
    flight_results: str
    hotel_results: str
    weather_results: str
    itinerary: str
    budget_results: str
    approval_request: str
    approved: bool
    human_feedback: str
    final_response: str
    llm_calls: int
