from unittest.mock import AsyncMock, Mock

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from tripmate.graph.builder import build_travel_graph


@pytest.fixture
def switch_graph(monkeypatch):
    import tripmate.graph.builder as builder

    monkeypatch.setattr(builder, "guardrail_agent", lambda state, llm: {"guardrail_allowed": True})
    monkeypatch.setattr(builder, "supervisor_agent", lambda state, llm: {"selected_agents": []})
    monkeypatch.setattr(builder, "create_itinerary_agent", lambda llm: lambda state: {
        "itinerary": "Draft Kyoto plan", "approval_request": "Approve?"
    })
    monkeypatch.setattr(builder, "create_final_agent", lambda llm: lambda state: {
        "final_response": "Polished Kyoto plan"
    })
    client = Mock()
    client.send_message = AsyncMock()
    client.write_room_document = AsyncMock()
    graph = build_travel_graph(MemorySaver(), Mock(), Mock(), client, "room-1")
    return graph, client


@pytest.mark.anyio
async def test_approval_writes_polished_final_plan_once(switch_graph):
    graph, client = switch_graph
    config = {"configurable": {"thread_id": "approved-thread"}}
    await graph.ainvoke({"user_query": "Kyoto trip", "switch_thread_id": "approved-thread"}, config)
    result = await graph.ainvoke(Command(resume={"approved": True, "feedback": "", "rejected": False}), config)
    assert result["final_response"] == "Polished Kyoto plan"
    client.write_room_document.assert_awaited_once_with("room-1", "Kyoto Trip Plan", "Polished Kyoto plan")


@pytest.mark.anyio
async def test_rejection_sends_hold_without_writing_document(switch_graph):
    graph, client = switch_graph
    config = {"configurable": {"thread_id": "rejected-thread"}}
    await graph.ainvoke({"user_query": "Kyoto trip", "switch_thread_id": "rejected-thread"}, config)
    await graph.ainvoke(Command(resume={"approved": False, "feedback": "", "rejected": True}), config)
    assert ("room-1", "HOLD — no changes made.") in [call.args for call in client.send_message.await_args_list]
    client.write_room_document.assert_not_awaited()


@pytest.mark.anyio
async def test_document_failure_sends_markdown_plan(switch_graph):
    graph, client = switch_graph
    client.write_room_document.side_effect = RuntimeError("document write failed")
    config = {"configurable": {"thread_id": "fallback-thread"}}
    await graph.ainvoke({"user_query": "Kyoto trip", "switch_thread_id": "fallback-thread"}, config)
    await graph.ainvoke(Command(resume={"approved": True, "feedback": "", "rejected": False}), config)
    assert ("room-1", "Polished Kyoto plan") in [call.args for call in client.send_message.await_args_list]
