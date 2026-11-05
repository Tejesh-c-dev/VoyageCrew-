"""LangGraph assembly for the travel planning workflow."""

from typing import Any
from time import perf_counter
from datetime import datetime, timezone

from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph

from tripmate.core.logging import (
    correlation_context,
    get_logger,
    hash_thread_id,
)
from tripmate.core.observability import (
    LLMInvocationTracker,
    agent_context,
    llm_invocation_context,
)
from tripmate.agents.approval import human_approval_agent
from tripmate.agents.budget import create_budget_agent
from tripmate.agents.final import create_final_agent
from tripmate.agents.flight import create_flight_agent
from tripmate.agents.guardrail import (
    guardrail_agent,
    guardrail_blocked_agent,
)
from tripmate.agents.hotel import create_hotel_agent
from tripmate.agents.itinerary import create_itinerary_agent
from tripmate.agents.supervisor import supervisor_agent
from tripmate.agents.weather import create_weather_agent
from tripmate.graph.routing import (
    ROUTE_MAP,
    route_after_agent,
    route_from_guardrail,
    route_from_supervisor,
)
from tripmate.graph.state import TravelState
from tripmate.integrations.mcp.client import MCPIntegration

logger = get_logger(__name__)


def _route_after_approval(state: TravelState) -> str:
    return "end" if state.get("rejected", False) else "final_agent"


def _observed_node(agent_name: str, node: Any):
    def run(
        state: TravelState,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        configurable = config.get("configurable") or {}
        metadata = config.get("metadata") or {}
        tracker = configurable.get("tripmate_llm_tracker")
        if not isinstance(tracker, LLMInvocationTracker):
            tracker = None
        correlation_id = metadata.get("correlation_id")
        thread_id = configurable.get("thread_id")
        started_at = perf_counter()

        with (
            correlation_context(correlation_id),
            llm_invocation_context(tracker),
            agent_context(agent_name),
        ):
            try:
                update = node(state)
            except GraphInterrupt:
                raise
            except Exception:
                logger.error(
                    "Workflow agent failed.",
                    extra={
                        "event": "workflow.agent.failed",
                        "agent": agent_name,
                        "thread_id_hash": (
                            hash_thread_id(thread_id)
                            if isinstance(thread_id, str)
                            else None
                        ),
                        "duration_ms": round(
                            (perf_counter() - started_at) * 1000,
                            2,
                        ),
                        "outcome": "failure",
                    },
                )
                raise

            if tracker is not None:
                update["llm_calls"] = tracker.snapshot()
            logger.info(
                "Workflow agent completed.",
                extra={
                    "event": "workflow.agent.completed",
                    "agent": agent_name,
                    "thread_id_hash": (
                        hash_thread_id(thread_id)
                        if isinstance(thread_id, str)
                        else None
                    ),
                    "duration_ms": round(
                        (perf_counter() - started_at) * 1000,
                        2,
                    ),
                    "outcome": "success",
                },
            )
            return update

    return run


def build_travel_graph(
    checkpointer: Any,
    llm: Any,
    integration: MCPIntegration,
    switch_client: Any = None,
    switch_room_id: str | None = None,
):
    def input_guardrail_node(state: TravelState) -> dict[str, Any]:
        return guardrail_agent(state, llm)

    def supervisor_node(state: TravelState) -> dict[str, Any]:
        return supervisor_agent(state, llm)

    def itinerary_node(state: TravelState) -> dict[str, Any]:
        update = create_itinerary_agent(llm)(state)
        if switch_client is not None and switch_room_id:
            import asyncio
            async def post_draft():
                await switch_client.send_message(
                    switch_room_id,
                    f"{update['itinerary']}\n\n{update['approval_request']}",
                )
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(post_draft())
            else:
                raise RuntimeError("Switch graph nodes require a synchronous graph execution context")
        return update

    def approval_node(state: TravelState) -> dict[str, Any]:
        update = human_approval_agent(state)
        if switch_client is not None and switch_room_id and update.get("rejected"):
            import asyncio
            asyncio.run(switch_client.send_message(switch_room_id, "HOLD — no changes made."))
        return update

    def final_node(state: TravelState) -> dict[str, Any]:
        update = create_final_agent(llm)(state)
        if switch_client is not None and switch_room_id:
            import asyncio
            async def publish():
                plan = update.get("final_response", "")
                try:
                    await switch_client.write_room_document(switch_room_id, "Kyoto Trip Plan", plan)
                except Exception:
                    await switch_client.send_message(switch_room_id, plan)
                timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                await switch_client.send_message(
                    switch_room_id,
                    f"Kyoto Trip Plan created. Thread: {state.get('switch_thread_id', 'unknown')}. Approved at: {timestamp}",
                )
            asyncio.run(publish())
        return update

    graph = StateGraph(TravelState)
    graph.add_node(
        "guardrail",
        _observed_node("guardrail", input_guardrail_node),
    )
    graph.add_node(
        "supervisor",
        _observed_node("supervisor", supervisor_node),
    )
    graph.add_node(
        "guardrail_blocked",
        _observed_node("guardrail_blocked", guardrail_blocked_agent),
    )
    graph.add_node(
        "flight_agent",
        _observed_node(
            "flight_agent",
            create_flight_agent(llm, integration),
        ),
    )
    graph.add_node(
        "hotel_agent",
        _observed_node(
            "hotel_agent",
            create_hotel_agent(integration),
        ),
    )
    graph.add_node(
        "weather_agent",
        _observed_node(
            "weather_agent",
            create_weather_agent(llm, integration),
        ),
    )
    graph.add_node(
        "budget_agent",
        _observed_node("budget_agent", create_budget_agent(llm)),
    )
    graph.add_node(
        "itinerary_agent",
        _observed_node("itinerary_agent", itinerary_node),
    )
    graph.add_node(
        "human_approval",
        _observed_node("human_approval", approval_node),
    )
    graph.add_node(
        "final_agent",
        _observed_node("final_agent", final_node),
    )

    graph.add_edge(START, "guardrail")
    graph.add_conditional_edges(
        "guardrail",
        route_from_guardrail,
        {
            "supervisor": "supervisor",
            "guardrail_blocked": "guardrail_blocked",
        },
    )
    graph.add_conditional_edges("supervisor", route_from_supervisor, ROUTE_MAP)
    graph.add_conditional_edges(
        "flight_agent", route_after_agent("flight_agent"), ROUTE_MAP
    )
    graph.add_conditional_edges(
        "hotel_agent", route_after_agent("hotel_agent"), ROUTE_MAP
    )
    graph.add_conditional_edges(
        "weather_agent", route_after_agent("weather_agent"), ROUTE_MAP
    )
    graph.add_conditional_edges(
        "budget_agent", route_after_agent("budget_agent"), ROUTE_MAP
    )
    graph.add_edge("itinerary_agent", "human_approval")
    graph.add_conditional_edges(
        "human_approval",
        _route_after_approval,
        {"end": END, "final_agent": "final_agent"},
    )
    graph.add_edge("final_agent", END)
    graph.add_edge("guardrail_blocked", END)

    return graph.compile(checkpointer=checkpointer)
