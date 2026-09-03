from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


def create_in_memory_checkpointer() -> InMemorySaver:
    """Create the phase-0 checkpointer with strict deserialization.

    Graph state is intentionally JSON-native. Blocking unregistered Python
    types now prevents a later LangGraph upgrade from silently changing how
    historical checkpoints are restored.
    """

    serializer = JsonPlusSerializer(allowed_msgpack_modules=None)
    return InMemorySaver(serde=serializer)
