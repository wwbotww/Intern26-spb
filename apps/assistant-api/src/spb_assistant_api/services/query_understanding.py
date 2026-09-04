from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from ..domain.agent_errors import AgentOperationError
from ..domain.failures import AgentFailure, FailureCategory
from ..domain.intents import Intent
from ..domain.ports import StructuredQueryUnderstandingModel
from ..domain.slots import (
    DeliveryTimeSlots,
    DevicePriceSlots,
    PolicySlots,
    PostageSlots,
    RegionRef,
    RegionResolution,
    SlotPayload,
    SlotProvenance,
    TrackingSlots,
    WeightValue,
)
from ..domain.understanding import (
    ControlDirective,
    IntentCandidate,
    QueryUnderstandingResult,
    StructuredModelUnderstanding,
)
from .region_resolver import (
    RegionMention,
    RegionResolver,
    create_demo_region_resolver,
)
from .slot_merger import required_missing_slots


_DOMESTIC_MAIL_NUMBER = re.compile(r"(?<!\d)(\d{13})(?!\d)")
_INTERNATIONAL_MAIL_NUMBER = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z]{2}\d{9}[A-Za-z]{2})(?![A-Za-z0-9])"
)
_WEIGHT = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+)?)\s*(千克|公斤|kg|斤|克|g)(?![A-Za-z])",
    re.IGNORECASE,
)
_TRACKING_KEYWORDS = ("轨迹", "物流", "邮件", "快递", "查件", "到哪")
_DELIVERY_KEYWORDS = (
    "时限",
    "多久",
    "几天",
    "多长时间",
    "什么时候到",
    "预计到达",
)
_POSTAGE_KEYWORDS = ("资费", "邮费", "运费", "计费", "费用")
_POLICY_KEYWORDS = (
    "政策",
    "法规",
    "标准",
    "理赔",
    "赔付",
    "赔偿",
    "规定",
    "条款",
    "办理材料",
)
_DEVICE_KEYWORDS = (
    "手机",
    "平板",
    "电脑",
    "设备",
    "iphone",
    "ipad",
    "华为",
    "小米",
    "荣耀",
    "三星",
)
_PRICE_KEYWORDS = ("价格", "参考价", "售价", "多少钱", "价钱")
_MULTI_CONNECTORS = (
    "同时",
    "另外",
    "以及",
    "并且",
    "还要",
    "还想",
    "顺便",
    "再查",
    "和",
)
_CANCEL_COMMANDS = frozenset({"取消", "算了", "退出", "停止查询"})
_RESTART_COMMANDS = frozenset({"重新开始", "重来", "清空", "重置"})


class RuleBasedQueryUnderstander:
    """Explainable five-intent parser with deterministic hard entities."""

    parser_version = "agent-rules-v2"

    def __init__(self, region_resolver: RegionResolver | None = None) -> None:
        self._regions = region_resolver or create_demo_region_resolver()

    async def understand(
        self,
        *,
        message: str,
        active_intent: Intent | None = None,
        explicit_intent: Intent | None = None,
        expected_slots: tuple[str, ...] = (),
    ) -> QueryUnderstandingResult:
        normalized = self.normalize(message)
        control = self._control(normalized)
        if control is not ControlDirective.NONE:
            return QueryUnderstandingResult(
                original_query=message,
                normalized_query=normalized,
                selected_intent=Intent.UNKNOWN,
                candidates=[],
                source="rules",
                parser_version=self.parser_version,
                control=control,
            )

        mail_no = self.extract_mail_number(normalized)
        weight, weight_ambiguities = self.extract_weight(normalized)
        mentions = self._regions.find_mentions(normalized)
        scores, signals = self._score_intents(
            normalized,
            mail_no=mail_no,
            weight=weight,
            region_count=len(mentions),
        )
        candidates = self._candidates(scores, signals)
        strong = [item for item in candidates if item.score >= 0.75]
        multi_intent = (
            len(strong) > 1
            and any(marker in normalized for marker in _MULTI_CONNECTORS)
        )
        ambiguities = list(weight_ambiguities)
        source = "rules"

        rule_selected = (
            candidates[0].intent
            if candidates and candidates[0].score >= 0.55
            else Intent.UNKNOWN
        )
        if explicit_intent is not None:
            selected = explicit_intent
            source = "explicit_ui"
            multi_intent = False
            candidates = self._with_priority_candidate(
                candidates,
                explicit_intent,
                signal="explicit_intent",
            )
            if (
                rule_selected not in {Intent.UNKNOWN, explicit_intent}
                and self._score_for(candidates, rule_selected) >= 0.80
            ):
                ambiguities.append("explicit_intent_conflict")
        elif active_intent is not None:
            alternate_score = self._score_for(candidates, rule_selected)
            if (
                rule_selected not in {Intent.UNKNOWN, active_intent}
                and alternate_score >= 0.80
            ):
                selected = rule_selected
                ambiguities.append("intent_switch_confirmation")
            else:
                selected = active_intent
                source = "active_workflow"
                candidates = self._with_priority_candidate(
                    candidates,
                    active_intent,
                    signal="active_workflow",
                )
        else:
            selected = rule_selected

        if multi_intent:
            ambiguities.append("multiple_intents")
        elif explicit_intent is None and len(candidates) >= 2:
            top, runner_up = candidates[:2]
            if top.score >= 0.55 and top.score - runner_up.score < 0.20:
                ambiguities.append("intent_selection_required")

        slots, missing, slot_ambiguities, provenance = self._extract_slots(
            selected,
            normalized,
            mail_no=mail_no,
            weight=(None if weight_ambiguities else weight),
            mentions=mentions,
            expected_slots=expected_slots,
        )
        ambiguities.extend(slot_ambiguities)
        return QueryUnderstandingResult(
            original_query=message,
            normalized_query=normalized,
            selected_intent=selected,
            candidates=candidates,
            slots=slots,
            slot_provenance=provenance,
            missing_slots=missing,
            ambiguities=list(dict.fromkeys(ambiguities)),
            multi_intent=multi_intent,
            source=source,
            parser_version=self.parser_version,
        )

    @staticmethod
    def normalize(message: str) -> str:
        return " ".join(message.strip().split())

    @staticmethod
    def extract_mail_number(message: str) -> str | None:
        domestic = _DOMESTIC_MAIL_NUMBER.search(message)
        if domestic:
            return domestic.group(1)
        international = _INTERNATIONAL_MAIL_NUMBER.search(message)
        if international:
            return international.group(1).upper()
        return None

    @staticmethod
    def extract_weight(
        message: str,
    ) -> tuple[WeightValue | None, list[str]]:
        values: list[WeightValue] = []
        for match in _WEIGHT.finditer(message):
            try:
                value = Decimal(match.group(1))
            except InvalidOperation:
                continue
            raw_unit = match.group(2).lower()
            if raw_unit == "斤":
                value *= Decimal("0.5")
                unit = "kg"
            elif raw_unit in {"克", "g"}:
                unit = "g"
            else:
                unit = "kg"
            try:
                values.append(WeightValue(value=value, unit=unit))
            except ValidationError:
                continue
        distinct = list(
            dict.fromkeys((item.value, item.unit) for item in values)
        )
        ambiguities = ["multiple_weights"] if len(distinct) > 1 else []
        return (values[0] if values else None), ambiguities

    @staticmethod
    def _control(message: str) -> ControlDirective:
        compact = message.replace(" ", "")
        if compact in _CANCEL_COMMANDS:
            return ControlDirective.CANCEL
        if compact in _RESTART_COMMANDS:
            return ControlDirective.RESTART
        return ControlDirective.NONE

    @staticmethod
    def _score_intents(
        message: str,
        *,
        mail_no: str | None,
        weight: WeightValue | None,
        region_count: int,
    ) -> tuple[dict[Intent, float], dict[Intent, list[str]]]:
        scores = {
            intent: 0.0
            for intent in Intent
            if intent is not Intent.UNKNOWN
        }
        signals = {intent: [] for intent in scores}

        def mark(intent: Intent, score: float, signal: str) -> None:
            scores[intent] = max(scores[intent], score)
            signals[intent].append(signal)

        if mail_no:
            mark(Intent.TRACKING, 0.98, "mail_no_pattern")
        if any(word in message for word in _TRACKING_KEYWORDS):
            mark(Intent.TRACKING, 0.86, "keyword_tracking")
        if any(word in message for word in _DELIVERY_KEYWORDS):
            mark(Intent.DELIVERY_TIME, 0.90, "keyword_delivery_time")
        if any(word in message for word in _POSTAGE_KEYWORDS):
            mark(Intent.POSTAGE, 0.84, "keyword_postage")
        if weight is not None:
            mark(Intent.POSTAGE, 0.90, "weight_entity")
        if region_count >= 2:
            if any(word in message for word in _DELIVERY_KEYWORDS):
                mark(Intent.DELIVERY_TIME, 0.95, "route_entities")
            if (
                weight is not None
                or any(word in message for word in _POSTAGE_KEYWORDS)
                or "多少钱" in message
            ):
                mark(Intent.POSTAGE, 0.95, "route_entities")
        if any(word in message for word in _POLICY_KEYWORDS):
            mark(Intent.POLICY, 0.88, "keyword_policy")
        lowered = message.lower()
        has_device = any(word in lowered for word in _DEVICE_KEYWORDS)
        has_price = any(word in message for word in _PRICE_KEYWORDS)
        if has_device and has_price:
            mark(Intent.DEVICE_PRICE, 0.94, "device_price_pair")
        elif has_device:
            mark(Intent.DEVICE_PRICE, 0.62, "keyword_device")
        return scores, signals

    @staticmethod
    def _candidates(
        scores: dict[Intent, float],
        signals: dict[Intent, list[str]],
    ) -> list[IntentCandidate]:
        candidates = [
            IntentCandidate(
                intent=intent,
                score=score,
                signals=list(dict.fromkeys(signals[intent])),
            )
            for intent, score in scores.items()
            if score > 0
        ]
        return sorted(
            candidates,
            key=lambda item: (-item.score, item.intent.value),
        )

    @staticmethod
    def _with_priority_candidate(
        candidates: list[IntentCandidate],
        intent: Intent,
        *,
        signal: str,
    ) -> list[IntentCandidate]:
        updated = [item for item in candidates if item.intent is not intent]
        existing = next(
            (item for item in candidates if item.intent is intent),
            None,
        )
        updated.append(
            IntentCandidate(
                intent=intent,
                score=1.0,
                signals=list(
                    dict.fromkeys(
                        [*(existing.signals if existing else []), signal]
                    )
                ),
            )
        )
        return sorted(
            updated,
            key=lambda item: (-item.score, item.intent.value),
        )

    @staticmethod
    def _score_for(
        candidates: list[IntentCandidate],
        intent: Intent,
    ) -> float:
        return next(
            (item.score for item in candidates if item.intent is intent),
            0.0,
        )

    @staticmethod
    def _route_regions(
        message: str,
        mentions: list[RegionMention],
        expected_slots: tuple[str, ...] = (),
    ) -> tuple[RegionRef | None, RegionRef | None]:
        if len(mentions) >= 2:
            return mentions[0].ref, mentions[1].ref
        if not mentions:
            return None, None
        mention = mentions[0]
        expected_regions = set(expected_slots) & {"origin", "destination"}
        if expected_regions == {"destination"}:
            return None, mention.ref
        if expected_regions == {"origin"}:
            return mention.ref, None
        prefix = message[: mention.start]
        if "从" in prefix or "寄件" in prefix or "始发" in prefix:
            return mention.ref, None
        if "到" in prefix or "收件" in prefix or "目的" in prefix:
            return None, mention.ref
        return mention.ref, None

    def _extract_slots(
        self,
        intent: Intent,
        message: str,
        *,
        mail_no: str | None,
        weight: WeightValue | None,
        mentions: list[RegionMention],
        expected_slots: tuple[str, ...],
    ) -> tuple[
        SlotPayload | None,
        list[str],
        list[str],
        list[SlotProvenance],
    ]:
        missing: list[str] = []
        ambiguities: list[str] = []
        provenance: list[SlotProvenance] = []
        if intent is Intent.UNKNOWN:
            return None, missing, ambiguities, provenance
        if intent is Intent.TRACKING:
            if mail_no is None:
                missing.append("mail_no")
            else:
                provenance.append(
                    SlotProvenance(
                        slot="mail_no",
                        source="rule_extractor",
                        raw_text=self._mask_mail_number(mail_no),
                    )
                )
            return (
                TrackingSlots(mail_no=mail_no),
                missing,
                ambiguities,
                provenance,
            )
        if intent in {Intent.POLICY, Intent.DEVICE_PRICE}:
            provenance.append(
                SlotProvenance(slot="question", source="current_turn")
            )
            slots: SlotPayload
            if intent is Intent.POLICY:
                slots = PolicySlots(question=message)
            else:
                slots = DevicePriceSlots(question=message)
            return slots, missing, ambiguities, provenance

        origin, destination = self._route_regions(
            message,
            mentions,
            expected_slots,
        )
        for name, value in (
            ("origin", origin),
            ("destination", destination),
        ):
            if (
                value is None
                or value.resolution is not RegionResolution.RESOLVED
            ):
                missing.append(name)
                if value is not None:
                    ambiguities.append(
                        f"region_{name}_{value.resolution.value}"
                    )
            else:
                provenance.append(
                    SlotProvenance(
                        slot=name,
                        source="rule_extractor",
                        raw_text=value.raw_text,
                    )
                )
        if intent is Intent.DELIVERY_TIME:
            return (
                DeliveryTimeSlots(origin=origin, destination=destination),
                missing,
                ambiguities,
                provenance,
            )
        if weight is None:
            missing.append("weight")
        else:
            provenance.append(
                SlotProvenance(slot="weight", source="rule_extractor")
            )
        return (
            PostageSlots(
                origin=origin,
                destination=destination,
                weight=weight,
            ),
            missing,
            ambiguities,
            provenance,
        )

    @staticmethod
    def _mask_mail_number(value: str) -> str:
        return f"***{value[-4:]}"


class StructuredLlmQueryUnderstander:
    """Schema gate around an injected structured-model adapter."""

    prompt_version = "query-understanding-v1"
    parser_version = "structured-model-v1"
    prompt = (
        "Classify one postal-assistant request into the provided intent schema. "
        "Return JSON only. Never return tool names, code, hidden reasoning, or "
        "facts not present in the user message."
    )

    def __init__(self, model: StructuredQueryUnderstandingModel) -> None:
        self._model = model

    async def understand(
        self,
        *,
        message: str,
        active_intent: Intent | None = None,
        explicit_intent: Intent | None = None,
        expected_slots: tuple[str, ...] = (),
    ) -> QueryUnderstandingResult:
        del active_intent, explicit_intent, expected_slots
        raw = await self._model.classify(
            message=message,
            prompt=self.prompt,
            prompt_version=self.prompt_version,
        )
        try:
            parsed = StructuredModelUnderstanding.model_validate(raw)
        except ValidationError as exc:
            raise AgentOperationError(
                AgentFailure(
                    category=FailureCategory.CONTRACT_VIOLATION,
                    code="query_model_schema_invalid",
                    message=(
                        "Query Understanding 模型输出未通过 schema 校验"
                    ),
                )
            ) from exc
        return QueryUnderstandingResult(
            original_query=message,
            normalized_query=RuleBasedQueryUnderstander.normalize(message),
            selected_intent=parsed.selected_intent,
            candidates=parsed.candidates,
            slots=parsed.slots,
            slot_provenance=self._model_provenance(parsed.slots),
            missing_slots=parsed.missing_slots,
            ambiguities=parsed.ambiguities,
            multi_intent=parsed.multi_intent,
            source="model",
            parser_version=self.parser_version,
            prompt_version=self.prompt_version,
        )

    @staticmethod
    def _model_provenance(
        slots: SlotPayload | None,
    ) -> list[SlotProvenance]:
        if slots is None:
            return []
        return [
            SlotProvenance(slot=name, source="model_extractor")
            for name, value in slots.model_dump().items()
            if name != "intent" and value is not None
        ]


class HybridQueryUnderstander:
    """Rules first; one schema-constrained model fallback when necessary."""

    parser_version = "hybrid-understanding-v1"

    def __init__(
        self,
        rules: RuleBasedQueryUnderstander | None = None,
        model_fallback: StructuredLlmQueryUnderstander | None = None,
    ) -> None:
        self._rules = rules or RuleBasedQueryUnderstander()
        self._model = model_fallback

    async def understand(
        self,
        *,
        message: str,
        active_intent: Intent | None = None,
        explicit_intent: Intent | None = None,
        expected_slots: tuple[str, ...] = (),
    ) -> QueryUnderstandingResult:
        rules = await self._rules.understand(
            message=message,
            active_intent=active_intent,
            explicit_intent=explicit_intent,
            expected_slots=expected_slots,
        )
        if not self._needs_fallback(rules) or self._model is None:
            return rules
        try:
            model = await self._model.understand(
                message=message,
                expected_slots=expected_slots,
            )
        except AgentOperationError as exc:
            return self._failed_fallback(
                rules,
                ambiguity=(
                    "model_output_invalid"
                    if exc.failure.code == "query_model_schema_invalid"
                    else "model_fallback_failed"
                ),
            )
        except Exception:
            return self._failed_fallback(
                rules,
                ambiguity="model_fallback_failed",
            )
        if model.selected_intent is not Intent.UNKNOWN:
            deterministic = await self._rules.understand(
                message=message,
                explicit_intent=model.selected_intent,
                expected_slots=expected_slots,
            )
            if deterministic.slots is not None:
                entity_ambiguities = [
                    item
                    for item in deterministic.ambiguities
                    if item == "multiple_weights"
                    or item.startswith("region_")
                ]
                model = model.model_copy(
                    update={
                        "slots": deterministic.slots,
                        "slot_provenance": deterministic.slot_provenance,
                        "missing_slots": required_missing_slots(
                            deterministic.slots
                        ),
                        "ambiguities": list(
                            dict.fromkeys(
                                [
                                    *model.ambiguities,
                                    *entity_ambiguities,
                                ]
                            )
                        ),
                    }
                )
        return model.model_copy(
            update={"parser_version": self.parser_version}
        )

    @staticmethod
    def _needs_fallback(result: QueryUnderstandingResult) -> bool:
        if result.control is not ControlDirective.NONE:
            return False
        if result.source in {"explicit_ui", "active_workflow"}:
            return False
        return result.selected_intent is Intent.UNKNOWN or (
            "intent_selection_required" in result.ambiguities
            and not result.multi_intent
        )

    def _failed_fallback(
        self,
        rules: QueryUnderstandingResult,
        *,
        ambiguity: str,
    ) -> QueryUnderstandingResult:
        return rules.model_copy(
            update={
                "ambiguities": list(
                    dict.fromkeys([*rules.ambiguities, ambiguity])
                ),
                "parser_version": self.parser_version,
            }
        )
