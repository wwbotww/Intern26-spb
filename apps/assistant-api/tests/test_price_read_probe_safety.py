import importlib.util
import io
import sys
import tarfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]


def probe_module():
    spec = importlib.util.spec_from_file_location(
        "price_read_probe_safety", ROOT / "deploy/price-query/remote_read_probe.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", ["../../outside.py", "/absolute.py", "unexpected.py"])
def test_remote_probe_rejects_unexpected_archive_before_source_execution(name, capsys):
    module = probe_module()
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        content = b"raise AssertionError('must never execute')"
        info = tarfile.TarInfo(name)
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    stream.seek(0)
    with pytest.raises(ValueError, match="unexpected probe archive"):
        module.load_bundle(stream)
    assert capsys.readouterr().out == ""


def test_remote_probe_rejects_oversized_bundle():
    with pytest.raises(ValueError, match="budget"):
        probe_module().load_bundle(io.BytesIO(b"x" * 1_000_001))


def test_remote_probe_loads_exact_checkout_sources_without_database_access(monkeypatch):
    import sqlalchemy

    monkeypatch.setattr(
        sqlalchemy,
        "create_engine",
        lambda *a, **k: pytest.fail("loading source must not connect"),
    )
    module = probe_module()
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for name in module.MODULES:
            path = module.PREFIX + name.replace(".", "/") + ".py"
            archive.add(ROOT / path, arcname=path)
    stream.seek(0)
    try:
        module.load_bundle(stream)
        loaded = sys.modules["price_probe.domain.product_price_query"]
        assert loaded.DevicePriceReadQuery(terms=("synthetic",)).kind == "device"
    finally:
        for name in list(sys.modules):
            if name == "price_probe" or name.startswith("price_probe."):
                sys.modules.pop(name, None)
