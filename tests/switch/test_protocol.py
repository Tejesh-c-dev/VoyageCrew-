import pytest

from tripmate.switch.protocol import (
    EventType,
    MessagePayload,
    RoomJoinPayload,
    SwitchMessageEvent,
    SwitchRoomJoinEvent,
    is_addressed,
    parse_event,
)


def test_valid_message_event_parses_with_typed_payload():
    raw = {
        "event_id": "evt-1",
        "event_type": "MESSAGE",
        "room_id": "room-1",
        "agent_id": "agent-1",
        "payload": {
            "addressed": True,
            "sender": "user-1",
            "sender_name": "A User",
            "message_id": "msg-1",
            "body": "Plan a trip",
            "timestamp": 1700000000,
            "thread_id": None,
            "attachments": [],
        },
    }

    event = parse_event(raw)

    assert isinstance(event, SwitchMessageEvent)
    assert event.event_type is EventType.MESSAGE
    assert isinstance(event.payload, MessagePayload)
    assert event.payload.body == "Plan a trip"
    assert event.payload.addressed is True


def test_valid_room_join_event_parses_with_typed_payload():
    raw = {
        "event_id": "evt-2",
        "event_type": "ROOM_JOIN",
        "room_id": "room-1",
        "agent_id": "agent-1",
        "payload": {
            "member": "user-1",
            "member_name": "A User",
            "timestamp": 1700000001,
            "listening": True,
        },
    }

    event = parse_event(raw)

    assert isinstance(event, SwitchRoomJoinEvent)
    assert event.event_type is EventType.ROOM_JOIN
    assert isinstance(event.payload, RoomJoinPayload)
    assert event.payload.listening is True


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {},
        {"event_id": "evt", "event_type": "UNKNOWN"},
        {
            "event_id": "evt",
            "event_type": "MESSAGE",
            "room_id": "room",
            "agent_id": "agent",
            "payload": {"addressed": True},
        },
    ],
)
def test_malformed_event_raises_value_error(raw):
    with pytest.raises(ValueError):
        parse_event(raw)


def test_is_addressed_only_accepts_explicitly_addressed_messages():
    addressed_message = parse_event(
        {
            "event_id": "evt-1",
            "event_type": "MESSAGE",
            "room_id": "room-1",
            "agent_id": "agent-1",
            "payload": {
                "addressed": True,
                "sender": "user-1",
                "sender_name": "A User",
                "message_id": "msg-1",
                "body": "Hello",
                "timestamp": 1700000000,
                "thread_id": None,
                "attachments": [],
            },
        }
    )
    unaddressed_message = parse_event(
        {
            "event_id": "evt-2",
            "event_type": "MESSAGE",
            "room_id": "room-1",
            "agent_id": "agent-1",
            "payload": {
                "addressed": False,
                "sender": "user-1",
                "sender_name": "A User",
                "message_id": "msg-2",
                "body": "Hello",
                "timestamp": 1700000001,
                "thread_id": None,
                "attachments": [],
            },
        }
    )
    room_join = parse_event(
        {
            "event_id": "evt-3",
            "event_type": "ROOM_JOIN",
            "room_id": "room-1",
            "agent_id": "agent-1",
            "payload": {
                "member": "user-1",
                "member_name": "A User",
                "timestamp": 1700000002,
                "listening": True,
            },
        }
    )

    assert is_addressed(addressed_message) is True
    assert is_addressed(unaddressed_message) is False
    assert is_addressed(room_join) is False
