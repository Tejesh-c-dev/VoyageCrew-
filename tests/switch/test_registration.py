import os
from unittest.mock import Mock

import pytest

from tripmate.switch.registration import AGENTS, AgentAlreadyExistsError, register_agents


def setup_env(monkeypatch):
    monkeypatch.setenv("SWITCH_API_URL", "https://switch.test/api")
    monkeypatch.setenv("SWITCH_ADMIN_API_KEY", "admin")


def test_registers_five_agents_and_writes_id_key_pairs(tmp_path, monkeypatch):
    setup_env(monkeypatch)
    session = Mock()
    session.post.side_effect = [Mock(status_code=201, json=lambda i=i: {"id": f"id-{i}", "api_key": f"key-{i}"}, raise_for_status=Mock()) for i in range(5)]
    env_file = tmp_path / ".env"
    result = register_agents(env_path=env_file, session=session)
    assert list(result) == ["flights", "stays", "weather", "budget", "itinerary"]
    for i, call in enumerate(session.post.call_args_list):
        body = call.kwargs["json"]
        assert body["name"] == list(AGENTS)[i]
        assert body["connector_type"] == "voyagecrew"
        assert body["status"] == "always_on" and body["message_exchange"] is True
        assert body["mediation"] == body["reporting"] == []
        assert body["profile"]["description"]
    assert len(env_file.read_text().splitlines()) == 10


def test_conflict_fails_once_without_overwrite(tmp_path, monkeypatch):
    setup_env(monkeypatch)
    session = Mock()
    session.post.return_value = Mock(status_code=409)
    with pytest.raises(AgentAlreadyExistsError):
        register_agents(env_path=tmp_path / ".env", session=session)
    session.post.assert_called_once()


def test_overwrite_uses_replacement_key_and_warns(tmp_path, monkeypatch):
    setup_env(monkeypatch)
    session = Mock()
    session.post.side_effect = [Mock(status_code=200, json=lambda i=i: {"id": f"new-{i}", "api_key": f"newkey-{i}"}, raise_for_status=Mock()) for i in range(5)]
    with pytest.warns(RuntimeWarning, match="old API key is now invalid"):
        result = register_agents(overwrite=True, env_path=tmp_path / ".env", session=session)
    assert result["flights"]["api_key"] == "newkey-0"
