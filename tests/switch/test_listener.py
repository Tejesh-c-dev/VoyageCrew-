from unittest.mock import AsyncMock, Mock

import pytest

from tripmate.switch.listener import VoyageCrewListener
from tripmate.switch.protocol import EventType, SwitchEvent


def message(body, *, sender="human", thread_id=None):
    return SwitchEvent(
        event_id="event-1",
        event_type=EventType.MESSAGE,
        room_id="room-1",
        agent_id="itinerary-agent",
        payload={"sender": sender, "body": body, "thread_id": thread_id},
    )


@pytest.fixture
def setup_listener():
    client = Mock(agent_id="itinerary-agent")
    graph = Mock()
    graph.ainvoke = AsyncMock(return_value={})
    listener = VoyageCrewListener({"itinerary": client}, graph, "room-1")
    return listener, graph


@pytest.mark.anyio
async def test_trip_request_starts_graph(setup_listener):
    listener, graph = setup_listener

    await listener.handle_message("itinerary", message("5 days in Kyoto"))

    graph.ainvoke.assert_awaited_once()
    invocation = graph.ainvoke.await_args.args[0]
    assert invocation["user_query"] == "5 days in Kyoto"
    assert invocation["messages"][0].content == "5 days in Kyoto"
    assert graph.ainvoke.await_args.kwargs["config"]["configurable"]["thread_id"] == "default"


@pytest.mark.anyio
async def test_approve_resumes_with_parsed_decision(setup_listener):
    listener, _ = setup_listener
    listener.resume_with_decision = AsyncMock()

    await listener.handle_message("itinerary", message("@itinerary APPROVE itinerary"))

    listener.resume_with_decision.assert_awaited_once_with("default", {"decision": "approve"})


@pytest.mark.anyio
async def test_hold_does_not_resume_graph(setup_listener):
    listener, graph = setup_listener
    listener.resume_with_decision = AsyncMock()

    await listener.handle_message("itinerary", message("maybe"))

    listener.resume_with_decision.assert_not_awaited()
    graph.ainvoke.assert_not_awaited()


@pytest.mark.anyio
async def test_reject_resumes_with_parsed_decision(setup_listener):
    listener, _ = setup_listener
    listener.resume_with_decision = AsyncMock()

    await listener.handle_message("itinerary", message("@itinerary REJECT"))

    listener.resume_with_decision.assert_awaited_once_with("default", {"decision": "reject"})


@pytest.mark.anyio
async def test_revise_resumes_with_instruction(setup_listener):
    listener, _ = setup_listener
    listener.resume_with_decision = AsyncMock()

    await listener.handle_message(
        "itinerary", message("@itinerary REVISE: swap Day 3 to Nara")
    )

    listener.resume_with_decision.assert_awaited_once_with(
        "default",
        {"decision": "revise", "instruction": "swap Day 3 to Nara"},
    )


@pytest.mark.anyio
async def test_resume_maps_reject_to_terminal_graph_state(setup_listener):
    listener, graph = setup_listener

    await listener.resume_with_decision("thread-1", {"decision": "reject"})

    command = graph.ainvoke.await_args.args[0]
    assert command.resume == {"approved": False, "feedback": "", "rejected": True}
    assert graph.ainvoke.await_args.kwargs["config"]["configurable"]["thread_id"] == "thread-1"


@pytest.mark.anyio
async def test_agent_messages_and_room_joins_are_ignored(setup_listener):
    listener, graph = setup_listener

    await listener.handle_message("itinerary", message("5 days in Kyoto", sender="itinerary-agent"))
    join = SwitchEvent(
        event_id="join-1",
        event_type=EventType.ROOM_JOIN,
        room_id="room-1",
        agent_id="itinerary-agent",
        payload={"member": "human"},
    )
    await listener.handle_message("itinerary", join)

    graph.ainvoke.assert_not_awaited()
