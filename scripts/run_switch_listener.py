"""Connect the VoyageCrew agents to a Switch room and run the listener."""

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tripmate.core.config import PROJECT_ROOT, configure_ssl_certificates, load_settings
from tripmate.core.llm import create_llm
from tripmate.graph.builder import build_travel_graph
from tripmate.integrations.mcp.client import create_mcp_integration
from tripmate.persistence.checkpointer import create_checkpointer
from tripmate.switch.client import SwitchAgentClient
from tripmate.switch.listener import VoyageCrewListener

AGENT_NAMES = ("flights", "stays", "weather", "budget", "itinerary")
logger = logging.getLogger("tripmate.switch.demo")


def _switch_config(parser: argparse.ArgumentParser) -> tuple[str, str, str, dict[str, tuple[str, str]]]:
    load_dotenv(PROJECT_ROOT / ".env")
    server = os.getenv("SWITCH_API_URL")
    room_id = os.getenv("SWITCH_ROOM_ID")
    room_owner_id = os.getenv("SWITCH_ROOM_OWNER_ID")
    if not server or not room_id or not isinstance(room_owner_id, str) or not room_owner_id.strip():
        parser.error("SWITCH_API_URL, SWITCH_ROOM_ID and SWITCH_ROOM_OWNER_ID are required (set them in .env)")
    agents = {}
    missing = []
    for name in AGENT_NAMES:
        prefix = f"SWITCH_AGENT_{name.upper()}"
        agent_id, key = os.getenv(f"{prefix}_ID"), os.getenv(f"{prefix}_KEY")
        if not agent_id or not key:
            missing.append(f"{prefix}_ID and {prefix}_KEY")
        else:
            agents[name] = (agent_id, key)
    if missing:
        parser.error("Missing Switch agent settings: " + ", ".join(missing))
    return server, room_id, room_owner_id, agents


async def run_listener(server: str, room_id: str, room_owner_id: str, credentials: dict[str, tuple[str, str]]) -> None:
    settings = load_settings(PROJECT_ROOT)
    configure_ssl_certificates()
    resources = None
    clients = {
        name: SwitchAgentClient(server, agent_id, key)
        for name, (agent_id, key) in credentials.items()
    }
    listener = None
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass
    try:
        await asyncio.gather(*(client.connect([room_id], filter="addressed") for client in clients.values()))
        for name in clients:
            logger.info("Connected Switch agent %s", name)
        resources = create_checkpointer(settings)
        integration = create_mcp_integration(settings)
        llm = create_llm(settings)
        graph = build_travel_graph(resources.saver, llm, integration,
                                   switch_client=clients["itinerary"], switch_room_id=room_id)
        listener = VoyageCrewListener(clients, graph, room_id, room_owner_id)
        listener_task = asyncio.create_task(listener.start(), name="switch-listener")
        stop_task = asyncio.create_task(stop.wait(), name="switch-stop")
        done, _ = await asyncio.wait((listener_task, stop_task), return_when=asyncio.FIRST_COMPLETED)
        if listener_task in done:
            await listener_task
        stop_task.cancel()
    finally:
        if listener is not None:
            tasks = [task for task in asyncio.all_tasks() if task.get_name() == "switch-listener"]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(*(client.close() for client in clients.values()), return_exceptions=True)
        if resources is not None:
            resources.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    server, room_id, room_owner_id, credentials = _switch_config(parser)
    try:
        asyncio.run(run_listener(server, room_id, room_owner_id, credentials))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
