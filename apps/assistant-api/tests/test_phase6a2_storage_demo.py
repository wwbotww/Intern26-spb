import asyncio
import json
from pathlib import Path

import yaml

from spb_assistant_api.storage_demo import drill
from spb_assistant_api.storage_snapshot import backup_store, restore_backup
from spb_assistant_api.settings import AssistantSettings


def test_finite_demo_runs_again_after_restore_without_calling_gateway_on_replay(tmp_path, monkeypatch):
    for key in ("QUERY_MODEL_ENABLED", "TRACKING_ENABLED", "OTEL_ENABLED"):
        monkeypatch.setenv("ASSISTANT_" + key, "true")
    source = tmp_path / "source"
    assert asyncio.run(drill(source, "seed"))["synthetic_tool_calls"] == 1
    backup_store(source, tmp_path / "backup")
    restore_backup(tmp_path / "backup", tmp_path / "restored")
    assert asyncio.run(drill(tmp_path / "restored", "resume"))["synthetic_tool_calls"] == 1
    assert asyncio.run(drill(tmp_path / "restored", "replay"))["synthetic_tool_calls"] == 0


def test_storage_compose_is_isolated_and_uses_named_volumes():
    root = Path(__file__).resolve().parents[3]
    document = yaml.safe_load((root / "deploy/storage/docker-compose.yml").read_text())
    assert set(document["services"]) == {"operator", "demo"}
    for service in document["services"].values():
        assert service["network_mode"] == "none" and service["read_only"] is True
        assert service["user"] == "10001:10001"
        assert "ports" not in service and "env_file" not in service and "environment" not in service
        assert len(service["volumes"]) == 3
    assert "${" not in json.dumps(document)
    assert "data/state/" in (root / ".gitignore").read_text()


def test_managed_storage_settings_remain_explicit(tmp_path):
    import pytest
    from pydantic import ValidationError

    assert not AssistantSettings().agent_managed_storage_enabled
    with pytest.raises(ValidationError):
        AssistantSettings(agent_managed_storage_enabled=True)
    settings = AssistantSettings(agent_enabled=True, api_keys="synthetic", agent_database_path=str(tmp_path / "store" / "agent.db"), agent_managed_storage_enabled=True)
    assert settings.agent_managed_storage_enabled
