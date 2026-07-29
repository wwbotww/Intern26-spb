from __future__ import annotations


class RetrievalError(RuntimeError):
    """在线检索失败。"""


class RetrievalNotReadyError(RetrievalError):
    """在线检索依赖尚未就绪。"""


class CollectionContractError(RetrievalError):
    """Milvus collection 与共享契约不一致。"""


class QueryTooLongError(RetrievalError):
    """查询超出 embedding 模型可接受的 token 数。"""


class ChatProviderError(RuntimeError):
    """大模型服务调用失败。"""
