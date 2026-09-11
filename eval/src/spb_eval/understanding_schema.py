"""Independent file contracts for component evaluation, not V2 responses."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

Intent = Literal[
    "policy", "device_price", "product_price", "tracking", "delivery_time", "postage", "unknown"
]
INTENTS = (
    "policy",
    "device_price",
    "product_price",
    "tracking",
    "delivery_time",
    "postage",
    "unknown",
)


def understanding_intent_labels(expected: set[str]) -> tuple[str, ...]:
    """Fixed business label sets for legacy, unified-price or mixed Gold.

    Never infer the label universe from predictions (which would move the
    denominator when an implementation makes a mistake).
    """
    return tuple(intent for intent in INTENTS if (
        (intent != "product_price" or "product_price" in expected)
        and (intent != "device_price" or "product_price" not in expected or "device_price" in expected)
    ))


Slot = Literal[
    "mail_no", "origin", "destination", "weight_kg", "price_category", "product_text", "brand",
    "capacity", "memory", "color", "commodity", "variety", "price_region", "market", "price_nature",
    "source_scope", "price_unit", "price_quantity", "price_time",
]
SLOTS = ("mail_no", "origin", "destination", "weight_kg", "price_category", "product_text", "brand",
         "capacity", "memory", "color", "commodity", "variety", "price_region", "market", "price_nature",
         "source_scope", "price_unit", "price_quantity", "price_time")
MissingSlot = Literal[
    "mail_no", "origin", "destination", "weight", "conditions.kind",
    "conditions.product_text", "conditions.brand", "conditions.commodity", "conditions.variety",
    "conditions.region_text", "conditions.market_text", "conditions.price_nature", "conditions.source_scope",
    "conditions.requested_unit", "conditions.specification", "conditions.specification.capacity",
    "conditions.specification.memory", "conditions.specification.color", "time",
]
Code = Annotated[
    str, StringConstraints(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,95}$")
]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
FiniteMs = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Count = Annotated[int, Field(ge=0, le=1_000_000, strict=True)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UnderstandingInput(Contract):
    message: str = Field(min_length=1, max_length=4000)
    active_intent: Intent | None = None
    explicit_intent: Intent | None = None
    expected_slots: list[MissingSlot] = Field(
        default_factory=list, max_length=16
    )

    @model_validator(mode="after")
    def validate_context(self):
        if not self.message.strip():
            raise ValueError("empty_message")
        if "unknown" in (self.active_intent, self.explicit_intent):
            raise ValueError("unknown_is_not_a_context_intent")
        if len(set(self.expected_slots)) != len(self.expected_slots):
            raise ValueError("duplicate_expected_slots")
        return self


def canonical_slot(name: str, value: str) -> str:
    value = value.strip()
    if not value or len(value) > 255:
        raise ValueError("invalid_slot_value")
    if name == "mail_no":
        return value.upper()
    if name in {"weight_kg", "price_quantity"}:
        try:
            number = Decimal(value)
        except InvalidOperation:
            raise ValueError("invalid_weight") from None
        if (
            not number.is_finite()
            or number <= 0
            or abs(number.adjusted()) > 32
        ):
            raise ValueError("invalid_weight")
        return format(number.normalize(), "f")
    return value


class UnderstandingGold(Contract):
    # None deliberately excludes ambiguous/multi-intent rows from single-label F1.
    intent: Intent | None
    candidate_intents: list[Intent] = Field(default_factory=list, max_length=5)
    multi_intent: bool = False
    control: Literal["none", "cancel", "restart"] = "none"
    slot_values: dict[Slot, str] = Field(default_factory=dict)
    score_slots: bool = True
    missing_slots: list[MissingSlot] | None = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_gold(self):
        candidates = self.candidate_intents
        if len(set(candidates)) != len(candidates) or "unknown" in candidates:
            raise ValueError("invalid_gold_candidates")
        if self.intent is None and (len(candidates) < 2 or self.score_slots):
            raise ValueError(
                "ambiguous_gold_requires_candidates_and_unscored_slots"
            )
        if self.multi_intent and (
            self.intent is not None or len(candidates) < 2
        ):
            raise ValueError("multi_intent_is_not_a_single_label")
        if self.control != "none" and (
            self.intent != "unknown" or self.multi_intent or self.slot_values
        ):
            raise ValueError("invalid_control_gold")
        if self.missing_slots is not None and len(
            set(self.missing_slots)
        ) != len(self.missing_slots):
            raise ValueError("duplicate_gold_missing_slots")
        allowed = {
            "product_price": set(SLOTS) - {"mail_no", "origin", "destination", "weight_kg"},
            "tracking": {"mail_no"},
            "delivery_time": {"origin", "destination"},
            "postage": {"origin", "destination", "weight_kg"},
        }.get(self.intent, set())
        if not set(self.slot_values) <= allowed:
            raise ValueError("gold_slots_do_not_match_intent")
        allowed_missing = {
            "weight" if name == "weight_kg" else name for name in allowed
        }
        if self.intent == "product_price":
            allowed_missing = {
                "conditions.kind", "conditions.product_text", "conditions.brand", "conditions.commodity", "conditions.variety",
                "conditions.region_text", "conditions.market_text", "conditions.price_nature", "conditions.source_scope",
                "conditions.requested_unit", "conditions.specification", "conditions.specification.capacity",
                "conditions.specification.memory", "conditions.specification.color", "time",
            }
        if not set(self.missing_slots or []) <= allowed_missing:
            raise ValueError("gold_missing_slots_do_not_match_intent")
        self.slot_values = {
            k: canonical_slot(k, v) for k, v in self.slot_values.items()
        }
        return self


class UnderstandingCase(Contract):
    schema_version: Literal["qu-case-v1"] = "qu-case-v1"
    id: Code
    group_id: Code
    split: Literal["development", "holdout"] = "development"
    provenance: Literal["synthetic", "private_reviewed"] = "synthetic"
    annotation_status: Literal["draft", "reviewed"] = "draft"
    category: Code
    tags: list[Code] = Field(default_factory=list, max_length=12)
    input: UnderstandingInput
    gold: UnderstandingGold

    @model_validator(mode="after")
    def validate_split(self):
        if self.split == "holdout" and (
            self.annotation_status != "reviewed" or "seen_smoke" in self.tags
        ):
            raise ValueError("holdout_requires_review_and_unseen_samples")
        return self


class Prediction(Contract):
    intent: Intent
    candidate_intents: list[Intent] = Field(max_length=6)
    multi_intent: bool
    control: Literal["none", "cancel", "restart"]
    slot_fingerprints: dict[Slot, Digest]
    missing_slots: list[MissingSlot]
    source: Literal["rules", "model", "active_workflow", "explicit_ui"]
    parser_version: Code
    prompt_version: Code | None = None
    needs_model_fallback: bool

    @model_validator(mode="after")
    def validate_prediction(self):
        if len(set(self.candidate_intents)) != len(self.candidate_intents):
            raise ValueError("duplicate_candidates")
        if (
            self.intent != "unknown"
            and self.intent not in self.candidate_intents
        ):
            raise ValueError("missing_selected_candidate")
        if len(set(self.missing_slots)) != len(self.missing_slots):
            raise ValueError("duplicate_missing_slots")
        if self.source == "model" and self.prompt_version is None:
            raise ValueError("missing_model_prompt_version")
        return self


class ModelCall(Contract):
    outcome: Literal["not_called", "success", "failure", "budget_exhausted"]
    duration_ms: FiniteMs | None = None
    failure_code: Code | None = None
    prompt_tokens: Count | None = None
    completion_tokens: Count | None = None
    total_tokens: Count | None = None

    @model_validator(mode="after")
    def validate_call(self):
        called = self.outcome in {"success", "failure"}
        if called != (self.duration_ms is not None):
            raise ValueError("model_duration_requires_call")
        if not called and any(
            v is not None
            for v in (
                self.prompt_tokens,
                self.completion_tokens,
                self.total_tokens,
            )
        ):
            raise ValueError("usage_without_call")
        if (
            self.outcome in {"failure", "budget_exhausted"}
            and self.failure_code is None
        ):
            raise ValueError("missing_failure_code")
        if (
            self.outcome in {"not_called", "success"}
            and self.failure_code is not None
        ):
            raise ValueError("unexpected_failure_code")
        if (
            self.total_tokens is not None
            and self.prompt_tokens is not None
            and self.completion_tokens is not None
            and self.total_tokens
            != self.prompt_tokens + self.completion_tokens
        ):
            raise ValueError("inconsistent_usage")
        return self


class UnderstandingObservation(Contract):
    type: Literal["observation"] = "observation"
    id: Code
    input_sha256: Digest
    status: Literal["ok", "error", "skipped"]
    duration_ms: FiniteMs
    error_code: Code | None = None
    prediction: Prediction | None = None
    model_call: ModelCall

    @model_validator(mode="after")
    def validate_observation(self):
        if (self.status == "ok") != (self.prediction is not None):
            raise ValueError("status_prediction_mismatch")
        if (self.status != "ok") != (self.error_code is not None):
            raise ValueError("status_error_mismatch")
        if (
            self.model_call.outcome == "budget_exhausted"
            and self.status != "skipped"
        ):
            raise ValueError("budget_must_be_skipped")
        if self.prediction and (self.prediction.source == "model") != (
            self.model_call.outcome == "success"
        ):
            raise ValueError("model_source_mismatch")
        return self


class ExportManifest(Contract):
    type: Literal["manifest"] = "manifest"
    schema_version: Literal["qu-observations-v1"]
    request_sha256: Digest
    dataset_sha256: Digest
    split: Literal["development", "holdout"]
    mode: Literal["rules", "hybrid"]
    transport: Literal["none", "live", "test_transport"]
    created_at: str = Field(max_length=64)
    producer_version: Code
    rules_version: Code
    parser_version: Code
    prompt_version: Code | None
    implementation_sha256: Digest
    configuration_sha256: Digest
    prompt_sha256: Digest
    model: Code | None
    timeout_seconds: FiniteMs | None
    max_tokens: Count | None
    max_model_calls: Count

    @model_validator(mode="after")
    def validate_mode(self):
        if self.mode == "rules" and (
            self.transport != "none"
            or self.max_model_calls != 0
            or self.model is not None
        ):
            raise ValueError("invalid_rules_manifest")
        if self.mode == "hybrid" and (
            self.transport == "none"
            or self.model is None
            or self.prompt_version is None
            or self.timeout_seconds is None
            or self.timeout_seconds <= 0
            or not self.max_tokens
            or not self.max_model_calls
        ):
            raise ValueError("invalid_hybrid_manifest")
        return self
