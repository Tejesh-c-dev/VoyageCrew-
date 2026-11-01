"""Small helpers shared by agent modules."""

import json
from typing import Any, Protocol

from langchain_core.messages import HumanMessage, SystemMessage

from tripmate.core.observability import invoke_llm
from tripmate.graph.state import TravelState


class AgentNode(Protocol):
    def __call__(self, state: TravelState) -> dict[str, Any]: ...


def invoke_text(llm: Any, system_prompt: str, user_prompt: str) -> str:
    response = invoke_llm(
        llm,
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
    )
    return str(response.content)


def parse_json_object(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("The model did not return a JSON object.")
    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("The model did not return a JSON object.")
    return parsed


def empty_constraints() -> dict[str, Any]:
    return {
        "destination": "",
        "origin": "",
        "duration": "",
        "budget": "",
        "travel_style": "",
        "special_preferences": [],
    }
