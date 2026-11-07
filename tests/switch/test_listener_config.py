import argparse

import pytest

from scripts.run_switch_listener import _switch_config


@pytest.mark.parametrize("owner", [None, "", " \t "])
def test_switch_config_rejects_missing_or_blank_owner(monkeypatch, owner):
    monkeypatch.setattr("scripts.run_switch_listener.load_dotenv", lambda *_args: None)
    monkeypatch.setenv("SWITCH_API_URL", "https://switch.example")
    monkeypatch.setenv("SWITCH_ROOM_ID", "room")
    if owner is None:
        monkeypatch.delenv("SWITCH_ROOM_OWNER_ID", raising=False)
    else:
        monkeypatch.setenv("SWITCH_ROOM_OWNER_ID", owner)

    with pytest.raises(SystemExit):
        _switch_config(argparse.ArgumentParser())
