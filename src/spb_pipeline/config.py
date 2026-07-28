from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value else default


@dataclass(frozen=True)
class Settings:
    base_url: str = os.getenv("SPB_BASE_URL", "https://www.spb.gov.cn").rstrip("/")
    channel_id: str = os.getenv(
        "SPB_CHANNEL_ID", "de7de12df24948b98dcb8420d3777c04"
    )
    channel_code: str = os.getenv("SPB_CHANNEL_CODE", "c100012")
    page_size: int = _env_int("SPB_PAGE_SIZE", 50)
    request_delay_seconds: float = _env_float(
        "SPB_REQUEST_DELAY_SECONDS", 0.8
    )
    max_retries: int = _env_int("SPB_MAX_RETRIES", 3)
    timeout_seconds: float = _env_float("SPB_TIMEOUT_SECONDS", 30.0)
    data_dir: Path = Path(os.getenv("SPB_DATA_DIR", "data"))

    @property
    def inventory_endpoint(self) -> str:
        return f"{self.base_url}/common/search/{self.channel_id}"

    @property
    def raw_inventory_dir(self) -> Path:
        return self.data_dir / "raw" / "inventory"

    @property
    def raw_html_dir(self) -> Path:
        return self.data_dir / "raw" / "html"

    @property
    def raw_attachment_dir(self) -> Path:
        return self.data_dir / "raw" / "attachments"

    @property
    def state_db(self) -> Path:
        return self.data_dir / "state" / "crawl.db"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def embeddings_path(self) -> Path:
        return self.processed_dir / "embeddings-m3e-base.npz"

    @property
    def embeddings_manifest_path(self) -> Path:
        return self.processed_dir / "embeddings-m3e-base.manifest.json"

    @property
    def ocr_dir(self) -> Path:
        return self.processed_dir / "ocr"

    def ensure_directories(self) -> None:
        for path in (
            self.raw_inventory_dir,
            self.raw_html_dir,
            self.raw_attachment_dir,
            self.state_db.parent,
            self.processed_dir,
            self.ocr_dir,
            self.reports_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
