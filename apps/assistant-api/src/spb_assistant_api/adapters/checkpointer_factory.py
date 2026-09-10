from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from ..storage_paths import runtime_store_guard


def create_in_memory_checkpointer() -> InMemorySaver:
    """Create the phase-0 checkpointer with strict deserialization.

    Graph state is intentionally JSON-native. Blocking unregistered Python
    types now prevents a later LangGraph upgrade from silently changing how
    historical checkpoints are restored.
    """

    serializer = JsonPlusSerializer(allowed_msgpack_modules=None)
    return InMemorySaver(serde=serializer)


@asynccontextmanager
async def create_sqlite_checkpointer(
    database_path: str | Path,
    *, managed_storage: bool = False,
) -> AsyncIterator[AsyncSqliteSaver]:
    """Open the local async checkpoint backend with strict serialization.

    The caller owns this context for the complete lifetime of every graph
    compiled against the yielded saver.
    """

    path = str(database_path)
    if not path.strip():
        raise ValueError("checkpoint database path 不能为空")
    serializer = JsonPlusSerializer(allowed_msgpack_modules=None)
    with runtime_store_guard(database_path, required=managed_storage):
        async with aiosqlite.connect(path) as connection:
            saver = AsyncSqliteSaver(connection, serde=serializer)
            await saver.setup()
            yield saver
