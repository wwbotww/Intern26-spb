from __future__ import annotations

from typing import Any


def append_events(
    current: list[dict[str, Any]] | None,
    updates: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    return [*(current or []), *(updates or [])]


def append_tool_calls(
    current: list[dict[str, Any]] | None,
    updates: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    return [*(current or []), *(updates or [])]
