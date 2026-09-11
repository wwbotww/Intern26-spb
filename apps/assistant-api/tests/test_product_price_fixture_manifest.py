"""The handoff corpus is self-contained; CI never imports the producer repo."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re


FIXTURES = Path(__file__).parent / "fixtures" / "product_price"


def test_price_fixture_manifest_pins_offline_consumer_evidence() -> None:
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["synthetic"] is True
    assert manifest["contract_id"] == "product-price-read-2026-09-11"
    assert manifest["authorization"] == {
        "requires_live_database": False,
        "requires_network": False,
        "requires_model": False,
        "production_writes": False,
    }
    producer = manifest["producer_snapshot"]
    assert producer["working_tree_dirty"] is True
    assert producer["live_database_verified_by_this_stage"] is False
    assert re.fullmatch(r"[a-f0-9]{40}", producer["head_commit"])
    for source in producer["files"]:
        # These hashes identify reviewed uncommitted source; do not resolve an
        # external worktree during tests or confuse them with a deployed version.
        assert not Path(source["path"]).is_absolute()
        assert ".." not in Path(source["path"]).parts
        assert re.fullmatch(r"[a-f0-9]{64}", source["sha256"])
    datasets = manifest["datasets"]
    assert {item["file"] for item in datasets} == {
        "read_records.json", "legacy_device_baseline.json",
    }
    assert len(datasets) == 2
    for dataset in datasets:
        content = (FIXTURES / dataset["file"]).read_bytes()
        assert sha256(content).hexdigest() == dataset["sha256"]
        parsed = json.loads(content)
        if dataset["file"] == "read_records.json":
            assert set(parsed) == set(dataset["records"])
        else:
            assert parsed["fixture_version"] == dataset["fixture_version"]
            assert {item["id"] for item in parsed["known_gaps"]} == set(dataset["known_target_gaps"])
