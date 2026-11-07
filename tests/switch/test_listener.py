from unittest.mock import AsyncMock, Mock

import pytest

from tripmate.switch.listener import VoyageCrewListener
from tripmate.switch.protocol import EventType, SwitchMessageEvent, SwitchEvent


def message(body, *, sender="human", thread_id=None):
    return SwitchMessageEvent(
        event_id="event-1",
        event_type=EventType.MESSAGE,
        room_id="room-1",
        agent_id="itinerary-agent",
        payload={
            "addressed": True, "sender": sender, "sender_name": sender,
            "message_id": "message-1", "body": body, "timestamp": 1,
            "thread_id": thread_id, "attachments": [],
        },
    )


@pytest.fixture
def setup_listener():
    client = Mock(agent_id="itinerary-agent")
    graph = Mock()
    graph.ainvoke = AsyncMock(return_value={})
    listener = VoyageCrewListener({"itinerary": client}, graph, "room-1", "owner")
    return listener, graph


@pytest.mark.anyio
async def test_trip_request_starts_graph(setup_listener):
    listener, graph = setup_listener

    await listener.handle_message("itinerary", message("5 days in Kyoto"))

    graph.ainvoke.assert_awaited_once()
    invocation = graph.ainvoke.await_args.args[0]
    assert invocation["user_query"] == "5 days in Kyoto"
    assert invocation["messages"][0].content == "5 days in Kyoto"
    thread_id = graph.ainvoke.await_args.kwargs["config"]["configurable"]["thread_id"]
    assert thread_id
    assert thread_id != "default"


@pytest.mark.anyio
async def test_approve_resumes_with_parsed_decision(setup_listener):
    listener, _ = setup_listener
    listener.resume_with_decision = AsyncMock()

    await listener.handle_message("itinerary", message("@itinerary APPROVE itinerary", sender="owner", thread_id="trip-1"))

    listener.resume_with_decision.assert_awaited_once_with("trip-1", {"decision": "approve"})


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

    await listener.handle_message("itinerary", message("@itinerary REJECT", sender="owner", thread_id="trip-1"))

    listener.resume_with_decision.assert_awaited_once_with("trip-1", {"decision": "reject"})


@pytest.mark.anyio
async def test_revise_resumes_with_instruction(setup_listener):
    listener, _ = setup_listener
    listener.resume_with_decision = AsyncMock()

    await listener.handle_message(
        "itinerary", message("@itinerary REVISE: swap Day 3 to Nara", sender="owner", thread_id="trip-1")
    )

    listener.resume_with_decision.assert_awaited_once_with(
        "trip-1",
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


@pytest.mark.anyio
async def test_unauthorized_approval_and_missing_thread_are_ignored(setup_listener):
    listener, graph = setup_listener
    listener.resume_with_decision = AsyncMock()
    await listener.handle_message("itinerary", message("APPROVE itinerary", sender="guest", thread_id="trip-1"))
    await listener.handle_message("itinerary", message("APPROVE itinerary", sender="owner"))
    listener.resume_with_decision.assert_not_awaited()
    graph.ainvoke.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("body", [
    "APPROVE itinerary",
    "REVISE: change hotel",
    "REJECT",
])
async def test_unauthorized_decisions_never_resume_checkpoint(setup_listener, body):
    listener, graph = setup_listener
    await listener.handle_message("itinerary", message(body, sender="guest", thread_id="trip-1"))
    graph.ainvoke.assert_not_awaited()


@pytest.mark.anyio
async def test_conflicting_token_and_switch_thread_metadata_is_rejected(setup_listener):
    listener, graph = setup_listener
    await listener.handle_message(
        "itinerary",
        message("APPROVE itinerary [thread:trip-token]", sender="owner", thread_id="trip-metadata"),
    )
    graph.ainvoke.assert_not_awaited()


@pytest.mark.anyio
async def test_independent_trip_requests_get_distinct_checkpoints(setup_listener):
    listener, graph = setup_listener
    await listener.handle_message("itinerary", message("trip one"))
    await listener.handle_message("itinerary", message("trip two"))
    ids = [call.kwargs["config"]["configurable"]["thread_id"] for call in graph.ainvoke.await_args_list]
    assert len(set(ids)) == 2


@pytest.mark.anyio
async def test_two_active_trip_replies_resume_only_their_checkpoints(setup_listener):
    listener, graph = setup_listener
    await listener.handle_message("itinerary", message("trip one"))
    await listener.handle_message("itinerary", message("trip two"))
    ids = [call.kwargs["config"]["configurable"]["thread_id"] for call in graph.ainvoke.await_args_list]
    assert len(set(ids)) == 2

    listener.resume_with_decision = AsyncMock()
    for checkpoint_id in ids:
        await listener.handle_message(
            "itinerary", message(f"APPROVE itinerary [thread:{checkpoint_id}]", sender="owner")
        )
    assert [call.args[0] for call in listener.resume_with_decision.await_args_list] == ids


@pytest.mark.anyio
async def test_approval_for_one_thread_resumes_only_that_thread(setup_listener):
    listener, graph = setup_listener
    listener.resume_with_decision = AsyncMock()
    await listener.handle_message("itinerary", message("APPROVE itinerary", sender="owner", thread_id="trip-a"))
    listener.resume_with_decision.assert_awaited_once_with("trip-a", {"decision": "approve"})
    graph.ainvoke.assert_not_awaited()


@pytest.mark.anyio
async def test_checkpoint_token_routes_reply_when_switch_thread_id_is_absent(setup_listener):
    listener, _ = setup_listener
    listener.resume_with_decision = AsyncMock()
    await listener.handle_message("itinerary", message("APPROVE itinerary [thread:trip-token]", sender="owner"))
    listener.resume_with_decision.assert_awaited_once_with(
        "trip-token", {"decision": "approve", "thread_id": "trip-token"}
    )


@pytest.mark.anyio
async def test_rejected_checkpoint_cannot_be_resumed_again(setup_listener):
    listener, graph = setup_listener
    await listener.resume_with_decision("rejected-trip", {"decision": "reject"})
    await listener.resume_with_decision("rejected-trip", {"decision": "approve"})
    assert graph.ainvoke.await_count == 1


@pytest.mark.anyio
async def test_new_request_reusing_rejected_thread_gets_fresh_checkpoint(setup_listener):
    listener, graph = setup_listener
    listener._rejected_thread_ids.add("reused")
    await listener.handle_message("itinerary", message("new trip", thread_id="reused"))
    checkpoint_id = graph.ainvoke.await_args.kwargs["config"]["configurable"]["thread_id"]
    assert checkpoint_id != "reused"
