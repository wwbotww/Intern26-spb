from __future__ import annotations

import os

import pytest
from spb_assistant_api.settings import AssistantSettings


@pytest.fixture(autouse=True)
def isolate_assistant_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests opt into synthetic dependencies, never a developer's credentials.

    Individual configuration tests may still set environment variables or pass
    an explicit _env_file after this fixture has isolated the default sources.
    """
    for name in tuple(os.environ):
        if name.upper().startswith("ASSISTANT_"):
            monkeypatch.delenv(name)
    monkeypatch.setitem(AssistantSettings.model_config, "env_file", None)
