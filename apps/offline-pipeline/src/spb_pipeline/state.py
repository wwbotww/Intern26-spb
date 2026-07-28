from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ResourceState:
    resource_id: str
    document_id: str
    parent_document_id: str | None
    kind: str
    source_url: str
    canonical_url: str
    local_path: str
    status: str
    http_status: int | None
    content_type: str
    content_length: int | None
    etag: str
    last_modified: str
    sha256: str
    retry_count: int
    error_message: str
    discovered_at: str
    fetched_at: str


class CrawlState:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        self.connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;

            CREATE TABLE IF NOT EXISTS resources (
                resource_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                parent_document_id TEXT,
                kind TEXT NOT NULL,
                source_url TEXT NOT NULL,
                canonical_url TEXT NOT NULL,
                local_path TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                http_status INTEGER,
                content_type TEXT NOT NULL DEFAULT '',
                content_length INTEGER,
                etag TEXT NOT NULL DEFAULT '',
                last_modified TEXT NOT NULL DEFAULT '',
                sha256 TEXT NOT NULL DEFAULT '',
                retry_count INTEGER NOT NULL DEFAULT 0,
                error_message TEXT NOT NULL DEFAULT '',
                discovered_at TEXT NOT NULL,
                fetched_at TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS resources_document_id_idx
              ON resources(document_id);
            CREATE INDEX IF NOT EXISTS resources_status_idx
              ON resources(status);
            CREATE INDEX IF NOT EXISTS resources_kind_idx
              ON resources(kind);
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "CrawlState":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def discover(
        self,
        *,
        resource_id: str,
        document_id: str,
        parent_document_id: str | None,
        kind: str,
        source_url: str,
        canonical_url: str,
        local_path: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO resources (
                resource_id, document_id, parent_document_id, kind,
                source_url, canonical_url, local_path, discovered_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(resource_id) DO UPDATE SET
                document_id=excluded.document_id,
                parent_document_id=excluded.parent_document_id,
                kind=excluded.kind,
                source_url=excluded.source_url,
                canonical_url=excluded.canonical_url,
                local_path=excluded.local_path
            """,
            (
                resource_id,
                document_id,
                parent_document_id,
                kind,
                source_url,
                canonical_url,
                local_path,
                utc_now(),
            ),
        )
        self.connection.commit()

    def get(self, resource_id: str) -> ResourceState | None:
        row = self.connection.execute(
            "SELECT * FROM resources WHERE resource_id = ?", (resource_id,)
        ).fetchone()
        return ResourceState(**dict(row)) if row else None

    def mark_success(
        self,
        resource_id: str,
        *,
        http_status: int,
        content_type: str,
        content_length: int,
        etag: str,
        last_modified: str,
        sha256: str,
    ) -> None:
        self.connection.execute(
            """
            UPDATE resources SET
                status='success',
                http_status=?,
                content_type=?,
                content_length=?,
                etag=?,
                last_modified=?,
                sha256=?,
                error_message='',
                fetched_at=?
            WHERE resource_id=?
            """,
            (
                http_status,
                content_type,
                content_length,
                etag,
                last_modified,
                sha256,
                utc_now(),
                resource_id,
            ),
        )
        self.connection.commit()

    def mark_failure(
        self,
        resource_id: str,
        *,
        error_message: str,
        http_status: int | None = None,
        status: str = "failed",
    ) -> None:
        self.connection.execute(
            """
            UPDATE resources SET
                status=?,
                http_status=?,
                retry_count=retry_count + 1,
                error_message=?,
                fetched_at=?
            WHERE resource_id=?
            """,
            (status, http_status, error_message[:2000], utc_now(), resource_id),
        )
        self.connection.commit()

    def all(self, *, kind: str | None = None) -> list[ResourceState]:
        if kind:
            rows = self.connection.execute(
                "SELECT * FROM resources WHERE kind=? ORDER BY document_id",
                (kind,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM resources ORDER BY kind, document_id"
            ).fetchall()
        return [ResourceState(**dict(row)) for row in rows]

    def counts(self) -> dict[str, int]:
        rows = self.connection.execute(
            """
            SELECT kind || ':' || status AS key, COUNT(*) AS count
            FROM resources
            GROUP BY kind, status
            """
        ).fetchall()
        return {row["key"]: row["count"] for row in rows}

    def reset_failed(self) -> int:
        cursor = self.connection.execute(
            """
            UPDATE resources
            SET status='pending', error_message=''
            WHERE status IN ('failed', 'blocked')
            """
        )
        self.connection.commit()
        return cursor.rowcount

    def as_dicts(self) -> list[dict[str, Any]]:
        return [state.__dict__.copy() for state in self.all()]
