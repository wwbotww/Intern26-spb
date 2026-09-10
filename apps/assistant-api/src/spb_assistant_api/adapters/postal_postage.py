from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from pydantic import ValidationError

from ..domain.agent_errors import AgentOperationError
from ..domain.commands import PostageCommand
from ..domain.failures import AgentFailure, FailureCategory
from ..domain.postage import PostageFeeItem, PostageQuoteBasis, PostageSource
from ..domain.postage_observation import PostageQuoteObservation
from ..domain.results import PostageData
from ..domain.slots import WeightValue
from ..services.circuit_breaker import CapabilityCircuitBreaker, CircuitState
from ..services.postage_preflight import PostagePreflight
from .agent_http import AgentJsonHttpClient, _reject_json_constant, _unique_json_object
from .postal_postage_contract import (
    POSTAL_POSTAGE_PATH, POSTAL_POSTAGE_URL,
    CsbPostageEnvelope, CsbPostageSigner, PostalPostageConfig,
    PostalPostageEnvelope, PostalPostagePayload, PostalPostageReply,
    PostalPostageRequest, PostalPostageSigner, postage_json,
)


# Fixed safe codes; supplier messages/identifiers must never enter an error.
_CSB_REASONS = {
    "500": "csb_internal", "501": "csb_not_authorized", "502": "csb_signature_rejected",
    "504": "csb_api_missing", "505": "csb_credentials_missing", "506": "csb_signature_missing",
    "507": "csb_parameters_missing", "508": "csb_secure_channel_required",
    "509": "csb_timestamp_missing", "510": "csb_timestamp_rejected",
    "800": "csb_protocol_failure", "801": "csb_provider_unreachable",
}
_BUSINESS_REASONS = {
    "001": "business_not_authorized", "002": "business_forbidden",
    "010": "business_failure", "020": "business_system_failure", "099": "business_unknown_failure",
}
_QUOTE_REASONS = {
    "0001": "weight_rejected", "0002": "product_rejected", "0003": "manual_required",
    "0005": "customer_eligibility", "0006": "customer_eligibility",
    "0007": "customer_history_unavailable", "0008": "billing_zone_unavailable",
    "0009": "rate_unavailable", "0010": "discount_rejected",
    "0011": "billing_mode_unsupported", "0016": "collection_context_incomplete",
}
_FEES = {
    "reg_fee": "registration", "insurance_fee": "insurance", "declared_value_fee": "declared_value",
    "inspection_fee": "inspection", "customs_fee": "customs", "fuel_fee": "fuel",
    "return_receipt_fee": "return_receipt", "password_delivery_fee": "password_delivery",
    "printing_fee": "printing", "handling_fee": "handling",
}
_MONEY = re.compile(r"[0-9]{1,9}(?:\.[0-9]{1,2})?", re.ASCII)
_GRAMS = re.compile(r"[1-9][0-9]{0,6}", re.ASCII)
_ZERO = re.compile(r"0+(?:\.0+)?", re.ASCII)
_SERIAL = re.compile(r"[A-Za-z0-9_.-]{1,36}", re.ASCII)


def _failure(
    code: str, message: str, category: FailureCategory = FailureCategory.CONTRACT_VIOLATION
) -> AgentOperationError:
    return AgentOperationError(AgentFailure(category=category, code=code, message=message))


class _QuotationRejected(AgentOperationError):
    """Known business refusal: a healthy protocol observation, never a quote."""


class PostalPostageGateway:
    """Single-attempt semantic adapter, restricted to injected MockTransport.

    P2 deliberately has no production network mode or configurable endpoint.
    The owned HTTP client never falls back to a fixture/network transport. P3
    must introduce a separately reviewed composition boundary for other modes.
    """

    def __init__(
        self, config: PostalPostageConfig, *, preflight: PostagePreflight,
        transport: httpx.MockTransport,
        clock: Callable[[], datetime] | None = None,
        serial_number_factory: Callable[[], str] | None = None,
        circuit_breaker: CapabilityCircuitBreaker | None = None,
    ) -> None:
        if not isinstance(transport, httpx.MockTransport):
            raise ValueError("P2 requires an explicit offline MockTransport")
        self._config = PostalPostageConfig.model_validate(config.model_dump())
        self._preflight = self._config.profile.bind(
            preflight.catalog, require_confirmation=preflight.require_confirmation,
        )
        if self._preflight.fingerprint != preflight.fingerprint:
            raise ValueError("postage preflight must be bound to this execution profile")
        self._source = PostageSource(
            source_type="fake_gateway", source_name="synthetic-postal-postage",
            profile=self._config.profile.name,
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._serial_number = serial_number_factory or (lambda: str(uuid4()))
        self._timezone = ZoneInfo(self._config.profile.timezone)
        self._business_signer = PostalPostageSigner(self._config.password)
        self._csb_signer = CsbPostageSigner(self._config.access_key, self._config.secret_key)
        self._breaker = circuit_breaker or CapabilityCircuitBreaker()
        self._closed = False
        self._client = AgentJsonHttpClient(
            base_url=POSTAL_POSTAGE_URL, timeout_seconds=self._config.timeout_seconds,
            max_connections=self._config.max_connections, verify_tls=True,
            max_response_bytes=self._config.max_response_bytes,
            manage_circuit_breaker=False, transport=transport,
        )

    async def quote(self, command: PostageCommand) -> PostageQuoteObservation:
        if self._closed or not self._config.enabled:
            raise _failure(
                "postal_postage_closed" if self._closed else "postal_postage_disabled",
                "资费适配器尚未启用或已关闭", FailureCategory.UPSTREAM_UNAVAILABLE,
            )
        if not isinstance(command, PostageCommand):
            raise _failure("postal_postage_command_invalid", "资费命令类型不正确")
        try:
            command = PostageCommand.model_validate(command.model_dump())
        except ValidationError:
            raise _failure("postal_postage_command_invalid", "资费命令不符合契约") from None
        self._preflight.validate_command(command)
        # Local clock/serialization/signing faults do not affect upstream health.
        serial, form, headers = self._request(command)
        await self._breaker.before_call("postage")
        try:
            response = await self._client.request_json(
                capability="postage", method="POST", path=POSTAL_POSTAGE_PATH,
                form_body=form, headers=headers,
            )
            observed = self._parse_response(command, response.payload, serial)
        except _QuotationRejected:
            # Verified refusal proves the protocol path is healthy (including a
            # half-open probe), but never yields a success result or a receipt.
            await asyncio.shield(self._breaker.record_success("postage"))
            raise
        except AgentOperationError as error:
            if error.failure.category in {
                FailureCategory.CONTRACT_VIOLATION, FailureCategory.UPSTREAM_UNAVAILABLE,
                FailureCategory.UPSTREAM_TIMEOUT, FailureCategory.UPSTREAM_RATE_LIMITED,
            }:
                await asyncio.shield(self._breaker.record_failure("postage"))
            else:
                await asyncio.shield(self._breaker.record_aborted("postage"))
            raise
        except BaseException:
            await asyncio.shield(self._breaker.record_aborted("postage"))
            raise
        await asyncio.shield(self._breaker.record_success("postage"))
        return observed

    def _request(self, command: PostageCommand) -> tuple[str, dict[str, str], dict[str, str]]:
        context = command.pricing_context
        assert context is not None  # validate_command has already enforced this.
        now = self._now()
        serial = self._serial_number()
        if not isinstance(serial, str) or not _SERIAL.fullmatch(serial):
            raise _failure("postal_postage_serial_invalid", "资费流水号生成失败", FailureCategory.INTERNAL_ERROR)
        try:
            request = PostalPostageRequest(
                productCode=context.product_code, weight=str(context.weight_grams),
                senderCityNo=context.origin_billing_id, aACode=context.destination_billing_id,
            )
            # Immutable strings are reused for both signatures and the form.
            message_map = request.message_map()
            form = {"map": message_map, "messageHeader": postage_json({
                "sysCode": self._config.sys_code, "sign": self._business_signer.sign(message_map),
                "serialNo": serial, "sendDate": now.astimezone(self._timezone).strftime("%Y-%m-%d %H:%M:%S"),
            })}
            elapsed = now - datetime(1970, 1, 1, tzinfo=UTC)
            timestamp_ms = elapsed.days * 86_400_000 + elapsed.seconds * 1000 + elapsed.microseconds // 1000
            headers = self._csb_signer.headers(
                form, api_name=self._config.profile.api_name,
                api_version=self._config.profile.api_version, timestamp_ms=timestamp_ms,
            )
        except (ValueError, UnicodeError):
            raise _failure("postal_postage_signing_unavailable", "无法使用已配置的资费签名方案", FailureCategory.UPSTREAM_UNAVAILABLE) from None
        return serial, form, headers

    def _parse_response(self, command: PostageCommand, payload: object, serial: str) -> PostageQuoteObservation:
        try:
            outer = CsbPostageEnvelope.model_validate(payload)
            if outer.code != "200":
                raise _failure(
                    "postal_postage_" + _CSB_REASONS.get(outer.code, "csb_unknown_failure"),
                    "资费网关未接受此次请求", FailureCategory.UPSTREAM_UNAVAILABLE,
                )
            envelope = PostalPostageEnvelope.model_validate(outer.body)
            if envelope.serial_no != serial:
                raise _failure("postal_postage_serial_mismatch", "资费响应与本次请求不匹配")
            datetime.strptime(envelope.ret_date, "%Y-%m-%d %H:%M:%S")
            if envelope.ret_code != "000":
                raise _failure(
                    "postal_postage_" + _BUSINESS_REASONS.get(envelope.ret_code, "business_unknown_failure"),
                    "资费业务服务未能完成请求", FailureCategory.UPSTREAM_UNAVAILABLE,
                )
            if not isinstance(envelope.ret_body, str):
                raise ValueError("retBody must be a single JSON string")
            decoded = json.loads(envelope.ret_body, object_pairs_hook=_unique_json_object, parse_constant=_reject_json_constant)
            wire = PostalPostagePayload.model_validate(decoded).quote
            if wire.error_code is not None:
                reason = _QUOTE_REASONS.get(wire.error_code)
                if reason is None:
                    raise _failure("postal_postage_quote_unknown_failure", "计费服务返回未识别的拒绝码")
                raise _QuotationRejected(AgentFailure(
                    category=FailureCategory.UPSTREAM_UNAVAILABLE,
                    code="postage_quote_" + reason, message="计费服务明确拒绝本次试算，未生成报价",
                ))
            if wire.error_name not in (None, ""):
                raise ValueError("success must not contain an error message")
            return self._observation(command, wire)
        except (ValueError, RecursionError):
            # Includes Pydantic errors/JSON duplicates. No body, signing header,
            # provider message, or validation input can escape via exception text.
            raise _failure("postal_postage_response_invalid", "资费响应不符合当前协议或金额契约") from None

    def _observation(self, command: PostageCommand, wire: PostalPostageReply) -> PostageQuoteObservation:
        context = command.pricing_context
        assert context is not None
        if _grams(wire.weight) != context.weight_grams:
            raise _failure("postal_postage_weight_mismatch", "资费响应称重与本次请求不匹配")
        billable = _grams(wire.fee_weight)
        for value in (wire.vol_weight, wire.vol_ratio):
            if value not in (None, "") and not _ZERO.fullmatch(value):
                raise _failure("postage_response_scope_unsupported", "当前实重询价无法解释计泡响应，未生成报价")
        # Check every supplied known price, even if it is not the display field.
        prices = {
            "total": _money(wire.total_fee), "standard": _money(wire.standard_fee),
            "customer": _money(wire.real_fee),
        }
        amount = prices[context.amount_kind]
        if amount is None:
            raise ValueError("selected amount field is missing; no fallback")
        fees = []
        for attr, kind in _FEES.items():
            value = _money(getattr(wire, attr))
            if value is not None:
                if value > 0 and kind != "fuel":
                    raise _failure("postage_response_scope_unsupported", "当前基础询价不能解释额外服务费用，未生成报价")
                fees.append(PostageFeeItem(kind=kind, amount=value))
        at = self._now()
        data = PostageData(
            # These are associated request conditions, not provider-returned
            # verification of product/route (the document defines no such echo).
            origin=command.origin, destination=command.destination, input_weight=command.weight,
            billable_weight=WeightValue(value=Decimal(billable), unit="g"),
            product_code=context.product_code, amount=amount, currency=context.currency,
            quote_basis=PostageQuoteBasis(context=context, fees=tuple(fees)), queried_at=at,
        )
        return PostageQuoteObservation(data=data, source=self._source, queried_at=at)

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise _failure("postal_postage_clock_invalid", "资费查询时钟必须包含时区", FailureCategory.INTERNAL_ERROR)
        return value.astimezone(UTC)

    async def readiness(self) -> str:
        """Local state only, not a supplier probe or production readiness claim."""
        if self._closed:
            return "not_ready"
        if not self._config.enabled:
            return "disabled"
        snapshot = await self._breaker.snapshot("postage")
        return "ready" if snapshot.state is CircuitState.CLOSED else "degraded"

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await self._client.close()


def _money(value: str | None) -> Decimal | None:
    if value is None:
        return None
    if not _MONEY.fullmatch(value):
        raise ValueError("money must be an explicit nonnegative two-decimal string")
    return Decimal(value)


def _grams(value: str | None) -> int:
    if value is None or not _GRAMS.fullmatch(value) or int(value) > 1_000_000:
        raise ValueError("weight must be positive integer grams within the local cap")
    return int(value)
