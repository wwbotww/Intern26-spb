from __future__ import annotations

from ..state import SpikeState


def complete_spike(state: SpikeState) -> dict[str, object]:
    if not state.get("mail_no", "").strip():
        raise ValueError("完成节点要求已收集邮件号")
    return {
        "phase": "completed",
        "reply": "阶段 0 LangGraph Spike 已完成参数收集。",
        "finish_reason": "stop",
        "audit_events": ["spike_completed"],
    }
