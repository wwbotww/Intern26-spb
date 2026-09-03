from __future__ import annotations

from langgraph.types import interrupt

from ...domain.agent_actions import (
    ClarificationRequest,
    RequiredInput,
    TrackingResume,
)
from ...domain.intents import Intent
from ..state import SpikeState


def clarify_tracking_number(state: SpikeState) -> dict[str, object]:
    del state
    request = ClarificationRequest(
        intent=Intent.TRACKING,
        prompt="请提供邮件号。",
        required_inputs=[
            RequiredInput(
                name="mail_no",
                label="邮件号",
                validation_hint="格式以轨迹接口最终契约为准",
            )
        ],
    )
    resumed = interrupt(request.model_dump(mode="json"))
    payload = TrackingResume.model_validate(resumed)
    return {
        "message": payload.mail_no,
        "mail_no": payload.mail_no,
        "phase": "understanding",
        "audit_events": ["clarification_resumed"],
    }
