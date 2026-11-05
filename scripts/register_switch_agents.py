"""Register the VoyageCrew agents with a Switch server."""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tripmate.switch.registration import register_agents


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overwrite", action="store_true", help="replace existing agents (their old keys will stop working)")
    args = parser.parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    if not os.getenv("SWITCH_API_URL"):
        parser.error("SWITCH_API_URL is required (set it in .env)")
    if not (os.getenv("SWITCH_ADMIN_API_KEY") or os.getenv("SWITCH_REGISTRATION_TOKEN")):
        parser.error("SWITCH_ADMIN_API_KEY (or SWITCH_REGISTRATION_TOKEN) is required (set it in .env)")
    agents = register_agents(overwrite=args.overwrite)
    print("Registered Switch agents: " + ", ".join(agents))
    print("Agent IDs and keys were saved to .env.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
