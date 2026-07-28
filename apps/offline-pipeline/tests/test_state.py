from __future__ import annotations

from spb_pipeline.state import CrawlState


def test_state_is_idempotent(tmp_path):
    database = tmp_path / "crawl.db"
    with CrawlState(database) as state:
        state.discover(
            resource_id="r1",
            document_id="d1",
            parent_document_id=None,
            kind="detail",
            source_url="http://example.test/a",
            canonical_url="https://example.test/a",
            local_path="/tmp/a",
        )
        state.mark_success(
            "r1",
            http_status=200,
            content_type="text/html",
            content_length=10,
            etag="etag",
            last_modified="",
            sha256="hash",
        )
        state.discover(
            resource_id="r1",
            document_id="d1",
            parent_document_id=None,
            kind="detail",
            source_url="http://example.test/a",
            canonical_url="https://example.test/a",
            local_path="/tmp/a",
        )

        resource = state.get("r1")

        assert resource is not None
        assert resource.status == "success"
        assert state.counts() == {"detail:success": 1}
