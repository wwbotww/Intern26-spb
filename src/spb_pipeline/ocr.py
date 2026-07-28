from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from .config import Settings
from .io_utils import read_jsonl, write_jsonl_atomic
from .normalize import clean_text


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _vision_binary() -> Path:
    binary = _project_root() / ".cache" / "vision_ocr"
    source = _project_root() / "scripts" / "vision_ocr.swift"
    if binary.exists() and binary.stat().st_mtime >= source.stat().st_mtime:
        return binary
    binary.parent.mkdir(parents=True, exist_ok=True)
    module_cache = Path(tempfile.gettempdir()) / "spb-swift-module-cache"
    subprocess.run(
        [
            "swiftc",
            "-O",
            "-module-cache-path",
            str(module_cache),
            "-framework",
            "Vision",
            "-framework",
            "AppKit",
            str(source),
            "-o",
            str(binary),
        ],
        check=True,
    )
    return binary


def _pdftoppm_binary() -> str:
    configured = os.getenv("PDFTOPPM_BIN")
    if configured:
        return configured
    discovered = shutil.which("pdftoppm")
    if discovered:
        return discovered
    raise RuntimeError("找不到 pdftoppm，请设置 PDFTOPPM_BIN")


def _recognize(binary: Path, image_path: Path) -> str:
    result = subprocess.run(
        [str(binary), str(image_path)],
        check=True,
        capture_output=True,
    )
    return clean_text(result.stdout.decode("utf-8", errors="replace"))


def _ocr_pdf(
    path: Path,
    *,
    vision_binary: Path,
    dpi: int,
) -> list[dict[str, Any]]:
    expected_pages = len(PdfReader(path).pages)
    with tempfile.TemporaryDirectory(prefix="spb-ocr-") as temp_name:
        output_prefix = Path(temp_name) / "page"
        subprocess.run(
            [
                _pdftoppm_binary(),
                "-r",
                str(dpi),
                "-jpeg",
                "-jpegopt",
                "quality=88",
                str(path),
                str(output_prefix),
            ],
            check=True,
            capture_output=True,
        )
        images = sorted(Path(temp_name).glob("page-*.jpg"))
        if len(images) != expected_pages:
            raise RuntimeError(
                f"{path}: 渲染 {len(images)} 页，预期 {expected_pages} 页"
            )
        return [
            {
                "type": "page",
                "page": page_number,
                "text": _recognize(vision_binary, image),
            }
            for page_number, image in enumerate(images, 1)
        ]


def run_ocr(
    settings: Settings,
    *,
    force: bool = False,
    limit: int | None = None,
    dpi: int = 160,
) -> dict[str, int]:
    documents = [
        document
        for document in read_jsonl(settings.processed_dir / "documents.jsonl")
        if document.get("parse_method") == "ocr_pending"
    ]
    if limit is not None:
        documents = documents[:limit]
    vision_binary = _vision_binary()
    counts = {"success": 0, "skipped": 0, "failed": 0, "pages": 0}
    for index, document in enumerate(documents, 1):
        output = settings.ocr_dir / f"{document['document_id']}.jsonl"
        if output.exists() and not force:
            blocks = list(read_jsonl(output))
            counts["skipped"] += 1
            counts["pages"] += len(blocks)
            print(
                f"OCR {index}/{len(documents)} skipped "
                f"{document['title']} ({len(blocks)} pages)",
                flush=True,
            )
            continue
        source = Path(document["metadata"]["local_path"])
        try:
            if source.suffix.lower() == ".pdf":
                blocks = _ocr_pdf(
                    source,
                    vision_binary=vision_binary,
                    dpi=dpi,
                )
            else:
                blocks = [
                    {
                        "type": "page",
                        "page": 1,
                        "text": _recognize(vision_binary, source),
                    }
                ]
            write_jsonl_atomic(output, blocks)
            counts["success"] += 1
            counts["pages"] += len(blocks)
            print(
                f"OCR {index}/{len(documents)} success "
                f"{document['title']} ({len(blocks)} pages)",
                flush=True,
            )
        except Exception as exc:
            counts["failed"] += 1
            print(
                f"OCR {index}/{len(documents)} failed "
                f"{document['title']}: {exc}",
                flush=True,
            )
    return counts
