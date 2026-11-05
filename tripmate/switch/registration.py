"""Register VoyageCrew specialist agents with Switch."""

import os
import warnings
from pathlib import Path

import requests

AGENTS = {
    "flights": "Finds and compares flight options, schedules, fares, and airport logistics.",
    "stays": "Finds suitable hotels and stays, comparing location, amenities, and prices.",
    "weather": "Provides destination weather forecasts and seasonal conditions for travel dates.",
    "budget": "Builds and checks trip budgets across transport, stays, activities, and daily costs.",
    "itinerary": "Combines travel details into a practical, day-by-day trip itinerary.",
}


class AgentAlreadyExistsError(Exception):
    """The agent exists and overwrite was not requested."""


def register_agents(*, overwrite: bool = False, env_path: str | Path = ".env", session=None) -> dict[str, dict[str, str]]:
    api_url = os.environ.get("SWITCH_API_URL", "").rstrip("/")
    admin_key = os.environ.get("SWITCH_ADMIN_API_KEY") or os.environ.get("SWITCH_REGISTRATION_TOKEN")
    if not api_url or not admin_key:
        raise ValueError("SWITCH_API_URL and SWITCH_ADMIN_API_KEY (or SWITCH_REGISTRATION_TOKEN) are required")
    if overwrite:
        warnings.warn("Overwriting Switch agents; the old API key is now invalid", RuntimeWarning)
    http = session or requests
    results = {}
    env_values = {}
    for name, description in AGENTS.items():
        response = http.post(
            f"{api_url}/agents",
            headers={"Authorization": f"Bearer {admin_key}"},
            json={
                "name": name,
                "description": description,
                "connector_type": "voyagecrew",
                "status": "always_on",
                "message_exchange": True,
                "mediation": [],
                "reporting": [],
                "profile": {"description": description},
                "overwrite": overwrite,
            },
        )
        if response.status_code == 409:
            if not overwrite:
                raise AgentAlreadyExistsError(f"Switch agent {name!r} already exists")
            warnings.warn(f"Replacing Switch agent {name!r}; its old API key is now invalid", RuntimeWarning)
        response.raise_for_status()
        data = response.json()
        agent_id, api_key = data["id"], data["api_key"]
        results[name] = {"id": str(agent_id), "api_key": str(api_key)}
        prefix = f"SWITCH_AGENT_{name.upper()}"
        env_values[f"{prefix}_ID"] = str(agent_id)
        env_values[f"{prefix}_KEY"] = str(api_key)

    path = Path(env_path)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = [line for line in existing.splitlines() if not any(line.startswith(key + "=") for key in env_values)]
    lines.extend(f"{key}={value}" for key, value in env_values.items())
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return results
