"""Bridge Switch room messages into the travel LangGraph workflow."""

import asyncio
import re
from uuid import uuid4
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from tripmate.switch.approval import parse_approval_decision
from tripmate.switch.protocol import EventType, SwitchEvent, SwitchMessageEvent


_APPROVAL_WORDS = re.compile(r"\b(?:approve|reject|revise)\b", re.IGNORECASE)
_AMBIGUOUS_REPLIES = {"maybe", "yes", "no", "ok", "okay", "sure"}


class VoyageCrewListener:
    def __init__(self, clients: dict[str, Any], graph: Any, room_id: str, room_owner_id: str):
        self.clients = clients
        self.graph = graph
        self.room_id = room_id
        self.room_owner_id = room_owner_id
        self._rejected_thread_ids: set[str] = set()
        self._completed_thread_ids: set[str] = set()

    async def start(self) -> None:
        """Listen on every configured client concurrently."""
        await asyncio.gather(*(self._listen(agent_name, client)
                               for agent_name, client in self.clients.items()))

    async def _listen(self, agent_name: str, client: Any) -> None:
        async for event in client.listen():
            await self.handle_message(agent_name, event)

    async def handle_message(self, agent_name: str, event: SwitchEvent) -> None:
        if not isinstance(event, SwitchMessageEvent) or not event.payload.addressed:
            return
        payload = event.payload
        sender = payload.sender
        own_agent_ids = {
            getattr(client, "agent_id", None) for client in self.clients.values()
        }
        own_agent_ids.discard(None)
        if sender in own_agent_ids or sender in self.clients:
            return

        body = payload.body
        parsed = parse_approval_decision(body)
        decision = parsed.get("decision")
        token_thread_id = parsed.get("thread_id")
        metadata_thread_id = payload.thread_id
        if token_thread_id and metadata_thread_id and token_thread_id != metadata_thread_id:
            return
        thread_id = token_thread_id or metadata_thread_id
        if decision in {"approve", "reject", "revise"}:
            if sender != self.room_owner_id or not isinstance(thread_id, str) or not thread_id.strip():
                return
            await self.resume_with_decision(thread_id, parsed)
            return

        # Malformed approval-shaped messages are ambiguous and must not trigger
        # either a resume or a fresh workflow. Other human text is a trip request.
        normalized_body = body.strip().casefold() if isinstance(body, str) else ""
        if (
            _APPROVAL_WORDS.search(body if isinstance(body, str) else "")
            or normalized_body in _AMBIGUOUS_REPLIES
        ):
            return
        if not isinstance(body, str) or not body.strip():
            return
        if not isinstance(thread_id, str) or not thread_id.strip() or thread_id in self._rejected_thread_ids:
            thread_id = uuid4().hex
        await self._start_graph(body, thread_id)

    async def _start_graph(self, body: str, thread_id: str) -> Any:
        invocation = {
            "messages": [HumanMessage(content=body)],
            "user_query": body,
            "switch_thread_id": thread_id,
        }
        config = {"configurable": {"thread_id": thread_id}}
        if hasattr(self.graph, "ainvoke"):
            return await self.graph.ainvoke(invocation, config=config)
        return await asyncio.to_thread(self.graph.invoke, invocation, config=config)

    async def resume_with_decision(self, thread_id: str, decision: dict) -> Any:
        """Resume the existing approval interrupt with a parsed Switch decision."""
        kind = decision.get("decision")
        if kind == "hold" or kind not in {"approve", "reject", "revise"}:
            return None
        if thread_id in self._completed_thread_ids:
            return None
        if kind == "reject":
            self._rejected_thread_ids.add(thread_id)
        resume_value = {
            "approved": kind == "approve",
            "feedback": decision.get("instruction", "") if kind == "revise" else "",
            "rejected": kind == "reject",
        }
        result = await self.graph.ainvoke(
            Command(resume=resume_value),
            config={"configurable": {"thread_id": thread_id}},
        )
        if kind in {"approve", "reject"}:
            self._completed_thread_ids.add(thread_id)
        return result

    async def post_agent_message(self, agent_name: str, text: str) -> Any:
        client = self.clients[agent_name]
        return await client.send_message(self.room_id, text)
