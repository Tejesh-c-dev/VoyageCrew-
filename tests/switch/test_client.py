from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlparse

import pytest

from tripmate.switch.client import (
    AuthenticationError,
    ProtocolError,
    RoomAccessError,
    SwitchAgentClient,
)
from tripmate.switch.protocol import SwitchEvent


class MockWebSocket:
    def __init__(self, frames=(), close_code=1000):
        self.frames = list(frames)
        self.close_code = close_code
        self.close = AsyncMock()
        self.send = AsyncMock()

    def __aiter__(self):
        async def iterate():
            for frame in self.frames:
                yield frame

        return iterate()


def message(event_id, addressed):
    return {
        "event_id": event_id,
        "event_type": "MESSAGE",
        "room_id": "room1",
        "agent_id": "agent",
        "payload": {
            "addressed": addressed,
            "sender": "user",
            "sender_name": "User",
            "message_id": f"msg-{event_id}",
            "body": "Hello",
            "timestamp": 1,
            "thread_id": None,
            "attachments": [],
        },
    }


def room_join(event_id, listening):
    return {
        "event_id": event_id,
        "event_type": "ROOM_JOIN",
        "room_id": "room1",
        "agent_id": "agent",
        "payload": {
            "member": "user",
            "member_name": "User",
            "timestamp": 1,
            "listening": listening,
        },
    }


@pytest.mark.anyio
async def test_url_and_authorization_header_are_built_correctly():
    socket = MockWebSocket()
    with patch("tripmate.switch.client.websockets.connect", new_callable=AsyncMock) as connect:
        connect.return_value = socket
        client = SwitchAgentClient("https://x.example", "agent", "secret", "fixed-uuid")
        await client.connect(["room1", "room2"])

    args, kwargs = connect.call_args
    assert args[0] == (
        "wss://x.example/agents/agent/connection/ws?connection_id=fixed-uuid"
        "&rooms=room1%2Croom2&filter=addressed"
    )
    assert kwargs["additional_headers"]["Authorization"] == "Bearer secret"


@pytest.mark.anyio
async def test_invalid_filter_fails_before_websocket_connect():
    client = SwitchAgentClient("https://x.example", "agent", "secret")
    with patch("tripmate.switch.client.websockets.connect", new_callable=AsyncMock) as connect:
        with pytest.raises(ValueError):
            await client.connect(["room1"], filter="foo")
    connect.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("code", "exception"),
    [(4400, ProtocolError), (4401, AuthenticationError), (4403, RoomAccessError), (4409, ProtocolError)],
)
async def test_permanent_close_codes_raise_without_retry(code, exception):
    socket = MockWebSocket(close_code=code)
    with patch("tripmate.switch.client.websockets.connect", new_callable=AsyncMock) as connect:
        connect.return_value = socket
        client = SwitchAgentClient("https://x.example", "agent", "secret")
        await client.connect(["room1", "room2"])
        with pytest.raises(exception) as error:
            await anext(client.listen())

    connect.assert_awaited_once()
    if code == 4403:
        assert "room1" in str(error.value) and "room2" in str(error.value)


@pytest.mark.anyio
async def test_server_restart_reconnects_with_same_connection_id():
    first = MockWebSocket(close_code=1012)
    second = MockWebSocket(close_code=1000)
    with (
        patch("tripmate.switch.client.websockets.connect", new_callable=AsyncMock) as connect,
        patch("tripmate.switch.client.asyncio.sleep", new_callable=AsyncMock) as sleep,
    ):
        connect.side_effect = [first, second]
        client = SwitchAgentClient("https://x.example", "agent", "secret")
        original_id = client.connection_id
        await client.connect(["room1", "room2"])
        assert [event async for event in client.listen()] == []

    assert connect.await_count == 2
    first_id = parse_qs(urlparse(connect.await_args_list[0].args[0]).query)["connection_id"]
    second_id = parse_qs(urlparse(connect.await_args_list[1].args[0]).query)["connection_id"]
    assert first_id == second_id == [original_id]
    sleep.assert_awaited_once_with(1)


@pytest.mark.anyio
async def test_normal_close_exits_cleanly():
    socket = MockWebSocket(close_code=1000)
    with patch("tripmate.switch.client.websockets.connect", new_callable=AsyncMock) as connect:
        connect.return_value = socket
        client = SwitchAgentClient("https://x.example", "agent", "secret")
        await client.connect(["room1"])
        assert [event async for event in client.listen()] == []


@pytest.mark.anyio
async def test_addressed_filter_yields_only_addressed_message_and_listening_join(caplog):
    frames = [
        message("addressed", True),
        message("unaddressed", False),
        room_join("listening", True),
        room_join("not-listening", False),
        {"event_id": "broken", "event_type": "MESSAGE", "payload": {}},
    ]
    socket = MockWebSocket(frames)
    with patch("tripmate.switch.client.websockets.connect", new_callable=AsyncMock) as connect:
        connect.return_value = socket
        client = SwitchAgentClient("https://x.example", "agent", "secret")
        await client.connect(["room1"], filter="addressed")
        events = [event async for event in client.listen()]

    assert len(events) == 2
    assert all(isinstance(event, SwitchEvent) for event in events)
    assert [event.event_id for event in events] == ["addressed", "listening"]
    assert "broken" in caplog.text


@pytest.mark.anyio
async def test_close_uses_normal_close_code():
    socket = MockWebSocket()
    with patch("tripmate.switch.client.websockets.connect", new_callable=AsyncMock) as connect:
        connect.return_value = socket
        client = SwitchAgentClient("https://x.example", "agent", "secret")
        await client.connect(["room1"])
        await client.close()
    socket.close.assert_awaited_once_with(code=1000)


@pytest.mark.anyio
async def test_connection_state_ping_and_cursor_are_handled_before_events():
    state = {"event_type": "connection_state", "payload": {"cursor": "c0", "generation": "g1"}}
    ping = {"event_type": "ping"}
    event = message("c1", True)
    socket = MockWebSocket([state, ping, event])
    with patch("tripmate.switch.client.websockets.connect", new_callable=AsyncMock) as connect:
        connect.return_value = socket
        client = SwitchAgentClient("https://x.example", "agent", "secret")
        await client.connect(["room1"])
        assert [x.event_id async for x in client.listen()] == ["c1"]
    socket.send.assert_awaited_once_with('{"event_type": "pong", "cursor": "c0"}')
    assert client.cursor == "c1" and client.generation == "g1"


@pytest.mark.anyio
async def test_reconnect_uses_cursor_generation_and_stable_id():
    first = MockWebSocket([
        {"event_type": "connection_state", "payload": {"cursor": "c0", "generation": "g1"}},
        message("c1", True),
    ], close_code=1011)
    second = MockWebSocket(close_code=1000)
    with (
        patch("tripmate.switch.client.websockets.connect", new_callable=AsyncMock) as connect,
        patch("tripmate.switch.client.asyncio.sleep", new_callable=AsyncMock),
    ):
        connect.side_effect = [first, second]
        client = SwitchAgentClient("https://x.example", "agent", "secret", "fixed")
        await client.connect(["room1"])
        assert len([x async for x in client.listen()]) == 1
    assert "start_from=c1" in connect.await_args_list[1].args[0]
    assert "expected_generation=g1" in connect.await_args_list[1].args[0]
    assert "connection_id=fixed" in connect.await_args_list[1].args[0]


@pytest.mark.anyio
async def test_send_message_posts_bearer_header_and_text_mentions():
    response = Mock(status_code=200, json=lambda: {"ok": True}, raise_for_status=Mock())
    post = AsyncMock(return_value=response)
    with patch("tripmate.switch.client.httpx.AsyncClient") as factory:
        factory.return_value.__aenter__ = AsyncMock(return_value=factory.return_value)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)
        factory.return_value.post = post
        client = SwitchAgentClient("https://x.example", "agent", "secret")
        assert await client.send_message("room1", "Hello", ["stays", "@weather"]) == {"ok": True}
    args, kwargs = post.call_args
    assert args[0] == "https://x.example/agents/agent/message"
    assert kwargs["headers"]["Authorization"] == "Bearer secret"
    assert kwargs["json"] == {"room_id": "room1", "content": "Hello @stays @weather"}


@pytest.mark.anyio
async def test_post_message_operation_uses_connection_header_and_unwrapped_body():
    response = Mock(status_code=200, json=lambda: {"ok": True}, raise_for_status=Mock())
    post = AsyncMock(return_value=response)
    with patch("tripmate.switch.client.httpx.AsyncClient") as factory:
        factory.return_value.__aenter__ = AsyncMock(return_value=factory.return_value)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)
        factory.return_value.post = post
        client = SwitchAgentClient("https://x.example", "agent", "secret")
        await client.post_message_operation("conn-1", {"name": "lookup", "result": 1})
    args, kwargs = post.call_args
    assert args[0] == "https://x.example/agents/agent/operation"
    assert kwargs["headers"] == {"Authorization": "Bearer secret", "X-Connection-ID": "conn-1"}
    assert kwargs["json"] == {"name": "lookup", "result": 1}


@pytest.mark.anyio
async def test_post_message_operation_rejects_missing_connection_before_request():
    client = SwitchAgentClient("https://x.example", "agent", "secret")
    with patch("tripmate.switch.client.httpx.AsyncClient") as factory:
        with pytest.raises(ValueError):
            await client.post_message_operation(None, {})
    factory.assert_not_called()
