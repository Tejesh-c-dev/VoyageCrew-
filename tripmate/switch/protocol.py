"""Data models for the Switch agent event protocol."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, ValidationError


class EventType(str, Enum):
    MESSAGE = "MESSAGE"
    COMMAND = "COMMAND"
    ROOM_JOIN = "ROOM_JOIN"


class MessagePayload(BaseModel):
    addressed: bool
    sender: str
    sender_name: str
    message_id: str
    body: str
    timestamp: int
    thread_id: str | None
    attachments: list


class RoomJoinPayload(BaseModel):
    member: str
    member_name: str
    timestamp: int
    listening: bool


class SwitchEvent(BaseModel):
    event_id: str
    event_type: EventType
    room_id: str
    agent_id: str
    payload: dict


class SwitchMessageEvent(SwitchEvent):
    event_type: EventType = EventType.MESSAGE
    payload: MessagePayload


class SwitchRoomJoinEvent(SwitchEvent):
    event_type: EventType = EventType.ROOM_JOIN
    payload: RoomJoinPayload


class ConnectionStatePayload(BaseModel):
    cursor: str | int | None = None
    generation: str | int | None = None


def parse_event(raw: dict) -> SwitchEvent:
    """Validate and parse a raw protocol event into its typed event model."""
    if not isinstance(raw, dict):
        raise ValueError("Switch event must be a dictionary")

    try:
        event_type = EventType(raw.get("event_type"))
        event_class: type[SwitchEvent]
        if event_type is EventType.MESSAGE:
            event_class = SwitchMessageEvent
        elif event_type is EventType.ROOM_JOIN:
            event_class = SwitchRoomJoinEvent
        else:
            event_class = SwitchEvent
        return event_class.model_validate(raw)
    except (ValueError, TypeError, ValidationError) as exc:
        raise ValueError("Malformed Switch event") from exc


def is_addressed(event: SwitchEvent) -> bool:
    """Return true only for messages explicitly addressed to this agent."""
    return (
        isinstance(event, SwitchMessageEvent)
        and event.event_type is EventType.MESSAGE
        and event.payload.addressed is True
    )
