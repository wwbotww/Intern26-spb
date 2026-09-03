from __future__ import annotations

import re

from ..state import SpikeState


_DEMO_MAIL_NUMBER = re.compile(r"(?<!\d)(\d{13})(?!\d)")


def understand_tracking_request(state: SpikeState) -> dict[str, object]:
    """Run the deterministic understanding step used by the phase-0 spike.

    The 13-digit pattern is deliberately a demo heuristic, not the final
    domain validation rule. The production rule remains provisional until the
    tracking API contract is available.
    """

    mail_no = state.get("mail_no", "").strip()
    if not mail_no:
        match = _DEMO_MAIL_NUMBER.search(state.get("message", ""))
        mail_no = match.group(1) if match else ""

    if mail_no:
        return {
            "mail_no": mail_no,
            "phase": "ready",
            "audit_events": ["query_understood"],
        }
    return {
        "phase": "clarifying",
        "audit_events": ["clarification_required"],
    }
