"""Internal unified Agent tool, borrowing the same V2 service as the V1 wrapper."""

from __future__ import annotations

from zoneinfo import ZoneInfo

from ..domain.agent_errors import AgentOperationError
from ..domain.exceptions import (
    PriceRepositoryUnavailableError,
    ProductPriceContractError,
    ProductPriceTimeoutError,
)
from ..domain.failures import AgentFailure, FailureCategory
from ..domain.intents import Intent
from ..domain.product_price_execution import ProductPriceData
from ..domain.product_price_slots import ProductPriceCommand
from ..domain.results import AgentResult, AgentResultStatus, SourceReference
from ..domain.tooling import CommandModel, argument_fingerprint
from ..services.fresh_price_scope import UnsupportedFreshScope
from ..services.product_price_preflight import PRODUCT_PRICE_DESCRIPTOR
from ..services.product_price_query import ProductPriceQueryService


def _failure(category, code, message, *, retryable=False):
    return AgentOperationError(
        AgentFailure(category=category, code=code, message=message, retryable=retryable)
    )


class ProductPriceTool:
    descriptor = PRODUCT_PRICE_DESCRIPTOR

    def __init__(self, service: ProductPriceQueryService):
        self._service = service

    async def execute(self, command: CommandModel) -> AgentResult:
        if not isinstance(command, ProductPriceCommand):
            raise _failure(
                FailureCategory.CONTRACT_VIOLATION,
                "price_command_invalid",
                "价格命令不符合契约",
            )
        try:
            quote = await self._service.quote_product(command)
        except ProductPriceTimeoutError:
            raise _failure(
                FailureCategory.UPSTREAM_TIMEOUT,
                "price_query_timeout",
                "价格查询超时",
                retryable=True,
            ) from None
        except PriceRepositoryUnavailableError:
            raise _failure(
                FailureCategory.UPSTREAM_UNAVAILABLE,
                "price_query_unavailable",
                "价格查询服务暂不可用",
                retryable=True,
            ) from None
        except ProductPriceContractError:
            raise _failure(
                FailureCategory.CONTRACT_VIOLATION,
                "price_contract_invalid",
                "价格数据不符合消费契约",
            ) from None
        except UnsupportedFreshScope as error:
            raise _failure(
                FailureCategory.INVALID_INPUT, "price_scope_unsupported", str(error)
            ) from None
        except (ValueError, TypeError):
            raise _failure(
                FailureCategory.CONTRACT_VIOLATION,
                "price_command_invalid",
                "价格命令或事实不符合契约",
            ) from None
        if quote.reason_code == "price_selection_identity_changed":
            raise _failure(
                FailureCategory.INVALID_INPUT,
                quote.reason_code,
                "所选商品身份已变化或失效，请重新查询。",
            )
        data = None
        provenance = []
        warnings = list(quote.warnings)
        reason = quote.reason_code
        if quote.status in {"quote", "candidates"}:
            data = ProductPriceData(
                mode=quote.status,
                facts=quote.facts,
                truncated=quote.truncated,
                command_fingerprint=argument_fingerprint(command),
            )
            provenance = [
                SourceReference(
                    source_type=fact.record.listing.source.source_type,
                    source_name=fact.record.listing.source.channel_name,
                    source_profile=fact.record.listing.source.channel_code,
                    source_url=str(fact.record.listing.source.source_url),
                    queried_at=fact.record.read_at,
                )
                for fact in quote.facts
            ]
        if quote.status in {"candidates", "need_more_info"}:
            status = AgentResultStatus.NEED_MORE_INFO
            answer = (
                "找到多个或不完整的候选，请选择所需商品，或补充查询条件。"
                if data
                else "请补充更具体的查询条件。"
            )
        elif quote.status == "no_match":
            status, answer = (
                AgentResultStatus.NO_MATCH,
                "本次未找到符合条件的价格记录。",
            )
        else:
            china = ZoneInfo("Asia/Shanghai")
            outdated = command.time.kind == "today" and any(
                fact.record.observation.observed_at.astimezone(china).date()
                != fact.record.read_at.astimezone(china).date()
                for fact in quote.facts
            )
            status = (
                AgentResultStatus.PARTIAL
                if outdated or quote.truncated
                else AgentResultStatus.SUCCESS
            )
            reason = "price_today_not_available" if outdated else reason
            answer = "已取得最新可用价格或商品状态，请核对规格、来源、口径和观察日期。"
        return AgentResult(
            tool=self.descriptor.tool_name,
            intent=Intent.PRODUCT_PRICE,
            status=status,
            answer=answer,
            data=data,
            provenance=provenance,
            warnings=warnings,
            reason_code=reason,
            missing_slots=list(quote.missing_slots),
        )
