from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from ..domain.agent_errors import AgentOperationError
from ..domain.commands import (
    DeliveryTimeCommand,
    DevicePriceCommand,
    PolicyCommand,
    PostageCommand,
    TrackingCommand,
)
from ..domain.failures import AgentFailure, FailureCategory
from ..domain.results import (
    AgentResult,
    AgentResultStatus,
    DeliveryTimeData,
    DevicePriceData,
    PolicyData,
    PostageData,
    TrackingData,
)
from ..domain.slots import RegionRef, RegionResolution
from ..domain.tooling import CommandModel


def _violation(code: str, message: str) -> AgentOperationError:
    return AgentOperationError(
        AgentFailure(
            category=FailureCategory.CONTRACT_VIOLATION,
            code=code,
            message=message,
        )
    )


class AgentResultValidator:
    def validate(
        self,
        *,
        command: CommandModel,
        result: AgentResult,
    ) -> AgentResult:
        if result.intent.value != command.intent:
            raise _violation(
                "result_command_intent_mismatch",
                "结果意图与执行命令不一致",
            )
        if isinstance(command, TrackingCommand):
            self._validate_tracking(command, result)
        elif isinstance(command, PolicyCommand):
            self._validate_policy(result)
        elif isinstance(command, DevicePriceCommand):
            self._validate_device_price(result)
        elif isinstance(command, DeliveryTimeCommand):
            self._validate_delivery_time(command, result)
        elif isinstance(command, PostageCommand):
            self._validate_postage(command, result)
        return result

    @staticmethod
    def _validate_policy(result: AgentResult) -> None:
        data = _require_evidence_data(
            result=result,
            expected_type=PolicyData,
            code_prefix="policy",
        )
        if data is None:
            return
        for item in data.evidence:
            if (
                not item.evidence_id.strip()
                or not item.title.strip()
                or not item.excerpt.strip()
                or not item.source_url.startswith(("http://", "https://"))
            ):
                raise _violation(
                    "policy_evidence_not_traceable",
                    "政策证据缺少可追溯字段",
                )

    @staticmethod
    def _validate_device_price(result: AgentResult) -> None:
        data = _require_evidence_data(
            result=result,
            expected_type=DevicePriceData,
            code_prefix="device_price",
        )
        if data is None:
            return
        for item in data.evidence:
            if (
                not item.evidence_id.strip()
                or not item.title.strip()
                or not item.model.strip()
                or not item.currency.strip()
                or not item.source.strip()
            ):
                raise _violation(
                    "device_price_evidence_not_traceable",
                    "设备价格证据缺少可追溯字段",
                )
            try:
                price = Decimal(item.price)
            except InvalidOperation as error:
                raise _violation(
                    "device_price_value_invalid",
                    "设备价格必须是可解析的十进制金额",
                ) from error
            if not price.is_finite() or price < 0:
                raise _violation(
                    "device_price_value_invalid",
                    "设备价格必须是非负有限金额",
                )
            try:
                observed_at = datetime.fromisoformat(
                    item.observed_at.replace("Z", "+00:00")
                )
            except ValueError as error:
                raise _violation(
                    "device_price_observed_at_invalid",
                    "设备价格观察时间格式无效",
                ) from error
            if not _is_aware(observed_at):
                raise _violation(
                    "device_price_observed_at_without_timezone",
                    "设备价格观察时间必须包含时区",
                )

    @staticmethod
    def _validate_tracking(
        command: TrackingCommand,
        result: AgentResult,
    ) -> None:
        if result.status in {
            AgentResultStatus.SUCCESS,
            AgentResultStatus.PARTIAL,
        }:
            if not isinstance(result.data, TrackingData):
                raise _violation(
                    "tracking_data_missing",
                    "轨迹成功结果必须包含类型化数据",
                )
            if result.data.mail_no != command.mail_no:
                raise _violation(
                    "tracking_mail_number_mismatch",
                    "轨迹结果邮件号与请求不一致",
                )
            occurred_at = [item.occurred_at for item in result.data.events]
            if any(not _is_aware(value) for value in occurred_at):
                raise _violation(
                    "tracking_event_time_without_timezone",
                    "轨迹节点时间必须包含时区",
                )
            if occurred_at != sorted(occurred_at):
                raise _violation(
                    "tracking_events_not_chronological",
                    "轨迹节点必须按时间正序返回",
                )
            if not _is_aware(result.data.queried_at):
                raise _violation(
                    "tracking_query_time_without_timezone",
                    "轨迹查询时间必须包含时区",
                )

    @staticmethod
    def _validate_delivery_time(
        command: DeliveryTimeCommand,
        result: AgentResult,
    ) -> None:
        if result.status not in {
            AgentResultStatus.SUCCESS,
            AgentResultStatus.PARTIAL,
        }:
            return
        if not isinstance(result.data, DeliveryTimeData):
            raise _violation(
                "delivery_time_data_missing",
                "时限成功结果必须包含类型化数据",
            )
        _validate_route(
            expected_origin=command.origin,
            expected_destination=command.destination,
            actual_origin=result.data.origin,
            actual_destination=result.data.destination,
            code_prefix="delivery_time",
        )
        if not _is_aware(result.data.queried_at):
            raise _violation(
                "delivery_time_query_time_without_timezone",
                "时限查询时间必须包含时区",
            )

    @staticmethod
    def _validate_postage(
        command: PostageCommand,
        result: AgentResult,
    ) -> None:
        if result.status not in {
            AgentResultStatus.SUCCESS,
            AgentResultStatus.PARTIAL,
        }:
            return
        if not isinstance(result.data, PostageData):
            raise _violation(
                "postage_data_missing",
                "资费成功结果必须包含类型化数据",
            )
        _validate_route(
            expected_origin=command.origin,
            expected_destination=command.destination,
            actual_origin=result.data.origin,
            actual_destination=result.data.destination,
            code_prefix="postage",
        )
        if result.data.input_weight != command.weight:
            raise _violation(
                "postage_input_weight_mismatch",
                "资费结果的输入重量与请求不一致",
            )
        if not _is_aware(result.data.queried_at):
            raise _violation(
                "postage_query_time_without_timezone",
                "资费查询时间必须包含时区",
            )


def _validate_route(
    *,
    expected_origin: RegionRef,
    expected_destination: RegionRef,
    actual_origin: RegionRef,
    actual_destination: RegionRef,
    code_prefix: str,
) -> None:
    if not _same_region(expected_origin, actual_origin):
        raise _violation(
            f"{code_prefix}_origin_mismatch",
            "结果寄件地区与请求不一致",
        )
    if not _same_region(expected_destination, actual_destination):
        raise _violation(
            f"{code_prefix}_destination_mismatch",
            "结果收件地区与请求不一致",
        )


def _same_region(expected: RegionRef, actual: RegionRef) -> bool:
    if (
        expected.resolution is not RegionResolution.RESOLVED
        or actual.resolution is not RegionResolution.RESOLVED
        or expected.canonical_name != actual.canonical_name
    ):
        return False
    for field_name in ("province_code", "city_code", "county_code"):
        expected_code = getattr(expected, field_name)
        actual_code = getattr(actual, field_name)
        if (
            expected_code is not None
            and actual_code is not None
            and expected_code != actual_code
        ):
            return False
    return True


def _require_evidence_data(
    *,
    result: AgentResult,
    expected_type: type[PolicyData] | type[DevicePriceData],
    code_prefix: str,
) -> PolicyData | DevicePriceData | None:
    if result.status not in {
        AgentResultStatus.SUCCESS,
        AgentResultStatus.PARTIAL,
    }:
        if result.data is not None or result.provenance:
            raise _violation(
                f"{code_prefix}_non_success_contains_facts",
                "非成功结果不能包含事实证据",
            )
        return None
    if not isinstance(result.data, expected_type):
        raise _violation(
            f"{code_prefix}_data_missing",
            "成功结果必须包含类型化证据数据",
        )
    if not result.data.evidence:
        raise _violation(
            f"{code_prefix}_evidence_missing",
            "成功结果必须包含完整证据",
        )
    evidence_ids = [item.evidence_id for item in result.data.evidence]
    provenance_ids = [item.record_id for item in result.provenance]
    if result.data.evidence_ids != evidence_ids:
        raise _violation(
            f"{code_prefix}_evidence_ids_mismatch",
            "证据索引与完整证据不一致",
        )
    if provenance_ids != evidence_ids:
        raise _violation(
            f"{code_prefix}_provenance_mismatch",
            "来源引用与证据顺序不一致",
        )
    return result.data


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
