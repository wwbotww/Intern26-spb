from __future__ import annotations

from collections import Counter
from statistics import mean

from pydantic import Field

from .understanding_dataset import digest
from .understanding_schema import (
    INTENTS,
    understanding_intent_labels,
    SLOTS,
    Contract,
    UnderstandingCase,
    UnderstandingObservation,
)

METRIC_VERSION = "qu-metrics-v1"


class UnderstandingThresholds(Contract):
    min_intent_macro_f1: float = Field(
        default=0.90, ge=0, le=1, allow_inf_nan=False
    )
    min_slot_micro_f1: float = Field(
        default=0.95, ge=0, le=1, allow_inf_nan=False
    )
    max_model_failure_rate: float = Field(
        default=0.05, ge=0, le=1, allow_inf_nan=False
    )


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _f1(tp: int, fp: int, fn: int) -> float | None:
    return _ratio(2 * tp, 2 * tp + fp + fn)


def _percentiles(values: list[float]) -> dict:
    values = sorted(values)

    def percentile(q):
        if not values:
            return None
        position = (len(values) - 1) * q
        low = int(position)
        high = min(low + 1, len(values) - 1)
        return values[low] + (values[high] - values[low]) * (position - low)

    return {
        "n": len(values),
        "p50_ms": percentile(0.50),
        "p95_ms": percentile(0.95),
    }


def slot_fingerprints(values: dict[str, str]) -> dict[str, str]:
    return {
        name: digest({"slot": name, "value": value})
        for name, value in values.items()
    }


def calculate_understanding_metrics(
    cases: list[UnderstandingCase],
    observations: dict[str, UnderstandingObservation],
    thresholds: UnderstandingThresholds | None = None,
) -> tuple[dict, list[dict]]:
    thresholds = thresholds or UnderstandingThresholds()
    # Preserve the frozen legacy five-business-intent comparison. The unified
    # price component replaces device_price, rather than adding an unused zero
    # class to every historical macro-F1 report. Mixed datasets retain both.
    expected_intents = {
        intent for case in cases
        for intent in (case.gold.intent, *case.gold.candidate_intents)
        if intent is not None
    }
    labels = understanding_intent_labels(expected_intents)
    confusion = {label: Counter() for label in INTENTS}
    slot_counts = {name: Counter(tp=0, fp=0, fn=0) for name in SLOTS}
    sources, statuses, model_outcomes, failure_codes = (
        Counter(),
        Counter(),
        Counter(),
        Counter(),
    )
    groups = {}
    rows = []
    label_count = label_correct = slot_exact = slot_cases = 0
    missing_correct = missing_cases = 0
    multi_correct = control_correct = successful_unknown = fallback_unknown = 0
    eligible_rules = 0
    latencies, model_latencies = [], []
    usage = {
        name: {"known_tokens": 0, "unknown_calls": 0}
        for name in ("prompt_tokens", "completion_tokens", "total_tokens")
    }
    for case in cases:
        observation = observations.get(case.id)
        prediction = observation.prediction if observation else None
        status = observation.status if observation else "missing"
        statuses[status] += 1
        reasons = []
        if status != "ok":
            reasons.append(
                "missing_observation"
                if observation is None
                else observation.error_code
            )
        gold = case.gold
        if gold.intent is not None:
            intent_ok = (
                prediction is not None and prediction.intent == gold.intent
            )
        else:
            intent_ok = prediction is not None and set(
                prediction.candidate_intents
            ) == set(gold.candidate_intents)
        if not intent_ok:
            reasons.append(
                "intent_mismatch"
                if gold.intent is not None
                else "candidate_set_mismatch"
            )
        if gold.intent is not None and gold.control == "none":
            label_count += 1
            label_correct += intent_ok
            confusion[gold.intent][
                prediction.intent if prediction else "__missing_or_error__"
            ] += 1
        for field in ("multi_intent", "control"):
            correct = prediction is not None and getattr(
                prediction, field
            ) == getattr(gold, field)
            if field == "multi_intent":
                multi_correct += correct
            else:
                control_correct += correct
            if not correct:
                reasons.append(f"{field}_mismatch")
        if gold.score_slots:
            slot_cases += 1
            expected = set(slot_fingerprints(gold.slot_values).items())
            actual = (
                set(prediction.slot_fingerprints.items())
                if prediction
                else set()
            )
            exact = prediction is not None and expected == actual
            slot_exact += exact
            if not exact:
                reasons.append("slot_mismatch")
            for bucket, values in (
                ("tp", expected & actual),
                ("fp", actual - expected),
                ("fn", expected - actual),
            ):
                for name, _ in values:
                    slot_counts[name][bucket] += 1
        if gold.missing_slots is not None:
            missing_cases += 1
            correct = prediction is not None and set(
                prediction.missing_slots
            ) == set(gold.missing_slots)
            missing_correct += correct
            if not correct:
                reasons.append("missing_slots_mismatch")
        if prediction:
            sources[prediction.source] += 1
            eligible_rules += prediction.needs_model_fallback
            if prediction.intent == "unknown" and prediction.source == "model":
                successful_unknown += 1
            if (
                prediction.intent == "unknown"
                and observation.model_call.outcome == "failure"
            ):
                fallback_unknown += 1
        if observation:
            latencies.append(observation.duration_ms)
            call = observation.model_call
            model_outcomes[call.outcome] += 1
            if call.outcome in {"success", "failure"}:
                model_latencies.append(call.duration_ms)
                for name in usage:
                    value = getattr(call, name)
                    if value is None:
                        usage[name]["unknown_calls"] += 1
                    else:
                        usage[name]["known_tokens"] += value
            if call.failure_code:
                failure_codes[call.failure_code] += 1
        row = {
            "id": case.id,
            "category": case.category,
            "case_sha256": digest(case.model_dump(mode="json")),
            "expected_intent": gold.intent,
            "observed_intent": prediction.intent if prediction else None,
            "status": status,
            "passed": not reasons,
            "reasons": reasons,
            "source": prediction.source if prediction else None,
            "model_outcome": observation.model_call.outcome
            if observation
            else "unobserved",
        }
        rows.append(row)
        for group in (
            f"category:{case.category}",
            f"split:{case.split}",
            *(f"tag:{tag}" for tag in case.tags),
        ):
            counter = groups.setdefault(group, Counter(total=0, passed=0))
            counter["total"] += 1
            counter["passed"] += not reasons

    per_class = {}
    for label in labels:
        tp = confusion[label][label]
        fn = sum(confusion[label].values()) - tp
        fp = sum(
            confusion[other][label] for other in INTENTS if other != label
        )
        per_class[label] = {
            "support": tp + fn,
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "f1": _f1(tp, fp, fn) or 0.0,
        }
    totals = {
        name: sum(count[name] for count in slot_counts.values())
        for name in ("tp", "fp", "fn")
    }
    called = model_outcomes["success"] + model_outcomes["failure"]
    summary = {
        "metric_version": METRIC_VERSION,
        "cases": {
            "total": len(cases),
            "passed": sum(row["passed"] for row in rows),
            "statuses": dict(statuses),
        },
        "intent": {
            "scored": label_count,
            "excluded_multi_ambiguous_or_control": len(cases) - label_count,
            "accuracy": _ratio(label_correct, label_count),
            "macro_f1": mean(item["f1"] for item in per_class.values()),
            "labels": list(labels),
            "per_class": per_class,
            "confusion": {label: dict(confusion[label]) for label in labels},
            "classes_with_support": sum(
                item["support"] > 0 for item in per_class.values()
            ),
        },
        "slots": {
            **totals,
            "micro_f1": _f1(**totals),
            "scored_cases": slot_cases,
            "exact_match_rate": _ratio(slot_exact, slot_cases),
            "per_slot": {
                name: {**counts, "f1": _f1(**counts)}
                for name, counts in slot_counts.items()
            },
        },
        "missing_slots": {
            "scored": missing_cases,
            "accuracy": _ratio(missing_correct, missing_cases),
        },
        "multi_intent_accuracy": _ratio(multi_correct, len(cases)),
        "control_accuracy": _ratio(control_correct, len(cases)),
        "model": {
            "calls": called,
            "call_rate": _ratio(called, len(cases)),
            "outcomes": dict(model_outcomes),
            "failure_codes": dict(failure_codes),
            "failure_rate_per_call": _ratio(model_outcomes["failure"], called),
            "failure_fallback_rate_per_case": _ratio(
                model_outcomes["failure"], len(cases)
            ),
            "normal_model_unknown": successful_unknown,
            "failure_fallback_unknown": fallback_unknown,
            "rules_fallback_eligible": eligible_rules,
            "sources": dict(sources),
            "latency": _percentiles(model_latencies),
            "usage": usage,
            "unobserved_case_rows": statuses["missing"],
        },
        "component_latency": _percentiles(latencies),
        "slices": {
            key: {
                **value,
                "pass_rate": _ratio(value["passed"], value["total"]),
            }
            for key, value in sorted(groups.items())
        },
    }
    checks = {
        "all_observations_ok": statuses["ok"] == len(cases),
        "all_six_intent_classes_present": summary["intent"][
            "classes_with_support"
        ]
        == 6,
        "intent_macro_f1": summary["intent"]["macro_f1"]
        >= thresholds.min_intent_macro_f1,
        "slot_micro_f1": summary["slots"]["micro_f1"] is not None
        and summary["slots"]["micro_f1"] >= thresholds.min_slot_micro_f1,
        "model_failure_rate": called == 0
        or model_outcomes["failure"] / called
        <= thresholds.max_model_failure_rate,
    }
    summary["quality_gate"] = {
        "passed": all(checks.values()),
        "checks": checks,
        "thresholds": thresholds.model_dump(),
    }
    return summary, rows
