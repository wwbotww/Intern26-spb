"""Run an explicitly supplied consumer read bundle in memory in the old API.

Operator-only diagnostic, NOT an HTTP endpoint or deployment mechanism. Input
is a source archive from this checkout, never user chat or a model response.
No extraction, file writes, credentials or raw record output. The database
target is exclusively the existing API container's ASSISTANT_MYSQL_DSN.
"""

import io
import json
import os
import sys
import tarfile
import types
import traceback
import threading
import time


MODULES = (
    "domain.exceptions",
    "domain.product_price",
    "domain.product_price_query",
    "adapters.product_price_rows",
    "adapters.mysql_product_price",
)
PREFIX = "apps/assistant-api/src/spb_assistant_api/"


def load_bundle(stream):
    data = stream.read(1_000_001)
    if len(data) > 1_000_000:
        raise ValueError("probe source archive exceeds its budget")
    for name in ("price_probe", "price_probe.domain", "price_probe.adapters"):
        module = types.ModuleType(name)
        module.__path__ = []
        sys.modules[name] = module
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        expected = {PREFIX + name.replace(".", "/") + ".py" for name in MODULES}
        members = archive.getmembers()
        if len(members) != len(expected) or {item.name for item in members} != expected:
            raise ValueError("unexpected probe archive members")
        for name in MODULES:
            path = PREFIX + name.replace(".", "/") + ".py"
            item = archive.getmember(path)
            if not item.isfile() or item.size > 300_000:
                raise ValueError("invalid probe source member")
            module = types.ModuleType("price_probe." + name)
            module.__package__ = module.__name__.rpartition(".")[0]
            sys.modules[module.__name__] = module
            exec(
                compile(archive.extractfile(item).read(), path, "exec"), module.__dict__
            )


def probe():
    from price_probe.adapters.mysql_product_price import MySQLProductPriceRepository
    from price_probe.domain.product_price_query import (
        DevicePriceReadQuery,
        FreshPriceReadQuery,
    )

    repository = MySQLProductPriceRepository(
        dsn=os.environ["ASSISTANT_MYSQL_DSN"],
        pool_size=1,
        connect_timeout_seconds=5,
        query_timeout_seconds=10,
    )
    try:
        # The old container may have no spare worker-thread capacity. Validate
        # the exact synchronous SQL/row boundary in this isolated process, not
        # the new async pool lifecycle (covered by separate integration tests).
        repository._initialize_sync(threading.Event(), time.monotonic() + 15)
        print(
            json.dumps(
                {"consumer_projection": "ready", "mode": "synchronous_read_boundary"}
            ),
            flush=True,
        )
        queries = [
            (
                brand,
                DevicePriceReadQuery(
                    terms=(term,),
                    brand_code=brand,
                    product_limit=1,
                    per_product_limit=2,
                ),
            )
            for brand, term in (
                ("APPLE", "iphone"),
                ("HUAWEI", "pura"),
                ("XIAOMI", "redmi"),
                ("OPPO", "find"),
                ("VIVO", "vivo"),
            )
        ] + [
            (
                nature,
                FreshPriceReadQuery(
                    terms=("黄瓜",), price_nature=nature, listing_limit=2
                ),
            )
            for nature in ("RETAIL_AVERAGE", "WHOLESALE_AVERAGE")
        ]
        for label, query in queries:
            try:
                batch = repository._search_sync(
                    query, threading.Event(), time.monotonic() + 10
                )
                print(
                    json.dumps(
                        {
                            "probe": label,
                            "validated_facts": len(batch.records),
                            "truncated": batch.truncated,
                        }
                    ),
                    flush=True,
                )
            except Exception as error:
                print(
                    json.dumps({"probe": label, "error_type": type(error).__name__}),
                    flush=True,
                )
    finally:
        if repository._engine is not None:
            repository._engine.dispose()
        repository._executor.shutdown(wait=True)


if __name__ == "__main__":
    try:
        load_bundle(sys.stdin.buffer)
        probe()
    except Exception as error:
        print(
            json.dumps(
                {
                    "probe_error_type": type(error).__name__,
                    "frames": [
                        [frame.name, frame.lineno]
                        for frame in traceback.extract_tb(error.__traceback__)[-8:]
                    ],
                }
            ),
            flush=True,
        )
        sys.exit(1)
