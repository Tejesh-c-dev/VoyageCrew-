"""Switch WebSocket and room operations client."""

import asyncio
import json
import logging
import uuid
from urllib.parse import quote

import websockets
import httpx
from websockets.exceptions import ConnectionClosed

from tripmate.switch.protocol import ConnectionStatePayload, SwitchEvent, SwitchMessageEvent, SwitchRoomJoinEvent, parse_event

logger = logging.getLogger(__name__)


class ProtocolError(Exception):
    """The server rejected the connection because of a protocol problem."""


class AuthenticationError(Exception):
    """The configured API key is invalid or revoked."""


class RoomAccessError(Exception):
    """The agent cannot serve one or more requested rooms."""


class SwitchAgentClient:
    def __init__(self, server_url: str, agent_id: str, api_key: str, connection_id: str | None = None):
        self.server_url = server_url.rstrip("/")
        self.agent_id = agent_id
        self.api_key = api_key
        self.connection_id = connection_id if connection_id is not None else str(uuid.uuid4())
        self.websocket = None
        self.room_ids: list[str] = []
        self.filter = "addressed"
        self.cursor = None
        self.generation = None

    def _url(self) -> str:
        scheme, separator, host = self.server_url.partition("://")
        if not separator or scheme not in {"http", "https", "ws", "wss"}:
            raise ValueError("server_url must start with http:// or https://")
        ws_scheme = "wss" if scheme in {"https", "wss"} else "ws"
        rooms = quote(",".join(self.room_ids), safe="")
        resume = f"&start_from={quote(str(self.cursor), safe='')}" if self.cursor is not None else ""
        generation = f"&expected_generation={quote(str(self.generation), safe='')}" if self.generation is not None else ""
        return (
            f"{ws_scheme}://{host}/agents/{quote(self.agent_id, safe='')}/connection/ws"
            f"?connection_id={quote(self.connection_id, safe='')}&rooms={rooms}&filter={self.filter}{resume}{generation}"
        )

    async def connect(self, room_ids: list[str], filter: str = "addressed") -> None:
        if filter not in {"all", "addressed"}:
            raise ValueError("filter must be 'all' or 'addressed'")
        self.room_ids = list(room_ids)
        self.filter = filter
        self.websocket = await websockets.connect(
            self._url(), additional_headers={"Authorization": f"Bearer {self.api_key}"}
        )

    async def close(self) -> None:
        if self.websocket is not None:
            await self.websocket.close(code=1000)
            self.websocket = None

    async def send_message(self, room_id: str, content: str, mentions: list[str] | None = None):
        """Post a message as this agent, representing mentions in text as @name."""
        if mentions:
            content = content.rstrip() + " " + " ".join(
                mention if mention.startswith("@") else f"@{mention}" for mention in mentions
            )
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.server_url}/agents/{quote(self.agent_id, safe='')}/message",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"room_id": room_id, "content": content},
            )
            response.raise_for_status()
            return response.json()

    async def post_message_operation(self, connection_id: str | None, arguments: dict):
        """Post an operation result for an active connection."""
        if not connection_id:
            raise ValueError("connection_id is required")
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.server_url}/agents/{quote(self.agent_id, safe='')}/operation",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "X-Connection-ID": connection_id,
                },
                json=arguments,
            )
            response.raise_for_status()
            return response.json()

    async def write_room_document(self, room_id: str, document_name: str, content: str):
        """Create a document in a room using the active Switch connection."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.server_url}/agents/{quote(self.agent_id, safe='')}/ops/create_room_document",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "X-Switch-Connection-Id": self.connection_id,
                },
                json={"room_id": room_id, "document_name": document_name, "content": content},
            )
            response.raise_for_status()
            return response.json()

    @staticmethod
    def _close_code(exc: ConnectionClosed) -> int | None:
        received = getattr(exc, "rcvd", None)
        return getattr(received, "code", None)

    def _raise_for_close(self, code: int | None, detail: str | None = None) -> None:
        suffix = f": {detail}" if detail else ""
        if code == 4400:
            raise ProtocolError("Switch rejected connection: check connection_id, scope, or filter" + suffix)
        if code == 4401:
            raise AuthenticationError("Switch rejected the API key (invalid or revoked)")
        if code == 4403:
            raise RoomAccessError(f"Switch cannot serve requested rooms: {self.room_ids}")
        if code == 4409:
            raise ProtocolError("Switch connection was taken over or has a protocol mismatch")

    async def listen(self):
        if self.websocket is None:
            raise RuntimeError("connect() must be called before listen()")

        reconnects = 0
        while True:
            try:
                async for raw_frame in self.websocket:
                    raw = None
                    try:
                        raw = json.loads(raw_frame) if isinstance(raw_frame, (str, bytes, bytearray)) else raw_frame
                        if isinstance(raw, dict) and raw.get("event_type") == "connection_state":
                            state = ConnectionStatePayload.model_validate(raw.get("payload", raw))
                            self.cursor, self.generation = state.cursor, state.generation
                            continue
                        if isinstance(raw, dict) and raw.get("event_type") == "ping":
                            await self.websocket.send(json.dumps({"event_type": "pong", "cursor": self.cursor}))
                            continue
                        if isinstance(raw, dict) and raw.get("event_type") == "refused":
                            detail = (raw.get("payload") or {}).get("detail") if isinstance(raw.get("payload"), dict) else raw.get("detail")
                            raise ProtocolError(f"Switch refused connection{': ' + str(detail) if detail else ''}")
                        event = parse_event(raw)
                    except (ValueError, TypeError, json.JSONDecodeError):
                        event_id = raw.get("event_id") if isinstance(raw, dict) else None
                        logger.warning("Skipping malformed Switch event event_id=%r raw_payload=%r", event_id, raw_frame)
                        continue

                    if self.cursor is None:
                        self.cursor = event.event_id
                        logger.warning("Switch connection_state missing; using first event sequence as cursor")
                    else:
                        self.cursor = event.event_id
                    if self.filter == "all":
                        yield event
                    elif isinstance(event, SwitchMessageEvent):
                        if event.payload.addressed is True:
                            yield event
                    elif isinstance(event, SwitchRoomJoinEvent):
                        if event.payload.listening is True:
                            yield event
                    # COMMAND is intentionally dropped on addressed connections.

                code = getattr(self.websocket, "close_code", 1000) or 1000
            except ConnectionClosed as exc:
                code = self._close_code(exc)

            self._raise_for_close(code)
            if code == 1000:
                return
            if code in {1011, 1012} and reconnects < 5:
                await asyncio.sleep(min(30, 2**reconnects))
                reconnects += 1
                await self.connect(self.room_ids, filter=self.filter)
                continue
            if code in {1011, 1012}:
                raise ProtocolError("Switch server restart: maximum reconnect attempts exceeded")
            return
