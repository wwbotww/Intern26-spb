from __future__ import annotations


class AssistantError(RuntimeError):
    """Base exception for expected assistant failures."""


class ToolUnavailableError(AssistantError):
    def __init__(self, tool: str) -> None:
        super().__init__(f"工具 {tool} 尚未接入")
        self.tool = tool


class ToolContractError(AssistantError):
    """Raised when a tool violates the registered contract."""


class PriceRepositoryError(AssistantError):
    """Raised when the read-only price source cannot complete a query."""


class PriceRepositoryUnavailableError(PriceRepositoryError):
    """Raised when the price database is not ready."""


class ProductPriceContractError(PriceRepositoryError):
    """V2 schema, source or evidence failed the consumer read contract."""


class ProductPriceTimeoutError(PriceRepositoryError):
    """The bounded V2 read did not finish within its execution budget."""


class PolicySourceError(AssistantError):
    """Raised when the policy HTTP source cannot complete a query."""


class PolicySourceUnavailableError(PolicySourceError):
    """Raised when the policy HTTP source is not ready."""


class PolicySourceContractError(PolicySourceError):
    """Raised when rag-api returns an invalid response contract."""
