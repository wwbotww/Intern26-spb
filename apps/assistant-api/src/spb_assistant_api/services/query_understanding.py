from __future__ import annotations

import re

from ..domain.intents import Intent
from ..domain.slots import TrackingSlots
from ..domain.understanding import (
    IntentCandidate,
    QueryUnderstandingResult,
)


_DOMESTIC_MAIL_NUMBER = re.compile(r"(?<!\d)(\d{13})(?!\d)")
_INTERNATIONAL_MAIL_NUMBER = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z]{2}\d{9}[A-Za-z]{2})(?![A-Za-z0-9])"
)
_TRACKING_KEYWORDS = ("轨迹", "物流", "邮件", "快递", "查件", "到哪")


class RuleBasedQueryUnderstander:
    """Offline phase-1 understanding for the tracking vertical slice."""

    parser_version = "tracking-rules-v1"

    async def understand(
        self,
        *,
        message: str,
        active_intent: Intent | None = None,
        explicit_intent: Intent | None = None,
    ) -> QueryUnderstandingResult:
        normalized = " ".join(message.strip().split())
        mail_no = self._extract_mail_number(normalized)
        source = "rules"

        if explicit_intent is not None:
            selected = explicit_intent
            source = "explicit_ui"
        elif active_intent is not None:
            selected = active_intent
            source = "active_workflow"
        elif mail_no or any(word in normalized for word in _TRACKING_KEYWORDS):
            selected = Intent.TRACKING
        else:
            selected = Intent.UNKNOWN

        if selected is Intent.TRACKING:
            signals = []
            if mail_no:
                signals.append("mail_no_pattern")
            if any(word in normalized for word in _TRACKING_KEYWORDS):
                signals.append("keyword_tracking")
            if source == "active_workflow":
                signals.append("active_workflow")
            slots = TrackingSlots(mail_no=mail_no)
            return QueryUnderstandingResult(
                original_query=message,
                normalized_query=normalized,
                selected_intent=selected,
                candidates=[
                    IntentCandidate(
                        intent=selected,
                        score=1.0 if explicit_intent else 0.95,
                        signals=signals,
                    )
                ],
                slots=slots,
                missing_slots=[] if mail_no else ["mail_no"],
                source=source,
                parser_version=self.parser_version,
            )

        return QueryUnderstandingResult(
            original_query=message,
            normalized_query=normalized,
            selected_intent=selected,
            candidates=[
                IntentCandidate(
                    intent=selected,
                    score=1.0 if explicit_intent else 0.0,
                    signals=["explicit_intent"] if explicit_intent else [],
                )
            ],
            source=source,
            parser_version=self.parser_version,
        )

    @staticmethod
    def _extract_mail_number(message: str) -> str | None:
        domestic = _DOMESTIC_MAIL_NUMBER.search(message)
        if domestic:
            return domestic.group(1)
        international = _INTERNATIONAL_MAIL_NUMBER.search(message)
        if international:
            return international.group(1).upper()
        return None
