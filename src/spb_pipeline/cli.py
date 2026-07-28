from __future__ import annotations

import argparse
import json
import os
import sys

from .chunker import chunk_documents
from .config import Settings
from .crawler import crawl_attachments, crawl_details
from .embedding import DEFAULT_MODEL, generate_embeddings, load_embedding_artifact
from .inventory import fetch_inventory
from .io_utils import write_jsonl_atomic
from .ocr import run_ocr
from .pipeline import load_documents, parse_documents
from .quality import build_quality_report
from .sinks.milvus import MilvusConfig, MilvusSink


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _chunk(settings: Settings, max_chars: int, overlap_chars: int) -> dict:
    documents = load_documents(settings)
    chunks = chunk_documents(
        documents,
        max_chars=max_chars,
        overlap_chars=overlap_chars,
    )
    write_jsonl_atomic(settings.processed_dir / "chunks.jsonl", chunks)
    return {
        "documents": len(documents),
        "chunks": len(chunks),
        "max_chars": max_chars,
        "overlap_chars": overlap_chars,
    }


def _add_milvus_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--uri",
        default=os.getenv("MILVUS_URI", ""),
        help="Milvus URI；也可用 MILVUS_URI",
    )
    parser.add_argument(
        "--database",
        default=os.getenv("MILVUS_DATABASE", "aisv"),
    )
    parser.add_argument(
        "--collection",
        default=os.getenv("MILVUS_COLLECTION", "spb_policy_chunks"),
    )
    parser.add_argument(
        "--token",
        default=os.getenv("MILVUS_TOKEN", ""),
        help="不会出现在命令输出中；也可用 MILVUS_TOKEN",
    )


def _milvus_config(args: argparse.Namespace) -> MilvusConfig:
    if not args.uri:
        raise ValueError("缺少 --uri 或 MILVUS_URI")
    uri = args.uri
    if "://" not in uri:
        uri = f"http://{uri}"
    return MilvusConfig(
        uri=uri,
        database=args.database,
        collection=args.collection,
        token=args.token,
    )


def _compact_milvus(description: dict) -> dict:
    if not description.get("exists"):
        return description
    schema = description.get("schema") or {}
    indexes = description.get("indexes") or {}
    return {
        "database": description["database"],
        "collection": description["collection"],
        "exists": True,
        "stats": description.get("stats"),
        "load_state": description.get("load_state"),
        "fields": [
            {
                "name": field.get("name"),
                "type": field.get("type"),
                "params": field.get("params"),
                "is_primary": field.get("is_primary", False),
            }
            for field in schema.get("fields") or []
        ],
        "indexes": {
            name: {
                "field_name": value.get("field_name"),
                "index_type": value.get("index_type"),
                "metric_type": value.get("metric_type"),
                "state": value.get("state"),
                "indexed_rows": value.get("indexed_rows"),
                "pending_index_rows": value.get("pending_index_rows"),
            }
            for name, value in indexes.items()
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spb-pipeline",
        description="国家邮政局政策法规标准抓取与结构化前置流水线",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("inventory", help="获取栏目完整清单")

    details = subparsers.add_parser("crawl-details", help="下载详情页")
    details.add_argument("--force", action="store_true")
    details.add_argument("--limit", type=int)
    details.add_argument(
        "--document-id",
        action="append",
        default=[],
        help="只抓指定 manuscriptId，可重复传入",
    )

    attachments = subparsers.add_parser(
        "crawl-attachments", help="下载已发现附件"
    )
    attachments.add_argument("--force", action="store_true")
    attachments.add_argument("--limit", type=int)
    attachments.add_argument(
        "--attachment-id",
        action="append",
        default=[],
        help="只抓指定 attachmentId，可重复传入",
    )

    subparsers.add_parser("parse", help="解析详情页及已下载附件")

    ocr = subparsers.add_parser("ocr", help="使用 macOS Vision 识别扫描附件")
    ocr.add_argument("--force", action="store_true")
    ocr.add_argument("--limit", type=int)
    ocr.add_argument("--dpi", type=int, default=160)

    chunk = subparsers.add_parser("chunk", help="生成 embedding 前置 chunks")
    chunk.add_argument("--max-chars", type=int, default=1200)
    chunk.add_argument("--overlap-chars", type=int, default=120)

    subparsers.add_parser("report", help="生成质量报告")

    embed = subparsers.add_parser("embed", help="生成本地 dense embeddings")
    embed.add_argument("--model", default=DEFAULT_MODEL)
    embed.add_argument("--batch-size", type=int, default=64)
    embed.add_argument("--device", choices=["cpu", "mps", "cuda"])

    milvus_create = subparsers.add_parser(
        "milvus-create", help="创建独立的政策 collection"
    )
    _add_milvus_arguments(milvus_create)
    milvus_create.add_argument("--dimension", type=int, default=768)

    milvus_ingest = subparsers.add_parser(
        "milvus-ingest", help="将 chunks 和 embeddings 写入空 collection"
    )
    _add_milvus_arguments(milvus_ingest)
    milvus_ingest.add_argument("--batch-size", type=int, default=128)

    milvus_sync = subparsers.add_parser(
        "milvus-sync", help="只向目标 collection 增量插入缺失 chunk"
    )
    _add_milvus_arguments(milvus_sync)
    milvus_sync.add_argument("--batch-size", type=int, default=128)

    milvus_check = subparsers.add_parser(
        "milvus-check", help="只读检查目标 collection"
    )
    _add_milvus_arguments(milvus_check)

    run = subparsers.add_parser("run", help="执行完整前置流水线")
    run.add_argument(
        "--skip-attachments",
        action="store_true",
        help="只抓详情页，不下载附件",
    )
    run.add_argument("--max-chars", type=int, default=1200)
    run.add_argument("--overlap-chars", type=int, default=120)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings()
    settings.ensure_directories()
    try:
        if args.command == "inventory":
            items, total = fetch_inventory(settings)
            _print({"total": total, "written": len(items)})
        elif args.command == "crawl-details":
            _print(
                crawl_details(
                    settings,
                    force=args.force,
                    limit=args.limit,
                    document_ids=set(args.document_id) or None,
                )
            )
        elif args.command == "crawl-attachments":
            _print(
                crawl_attachments(
                    settings,
                    force=args.force,
                    limit=args.limit,
                    attachment_ids=set(args.attachment_id) or None,
                )
            )
        elif args.command == "parse":
            _print(parse_documents(settings))
        elif args.command == "ocr":
            _print(
                run_ocr(
                    settings,
                    force=args.force,
                    limit=args.limit,
                    dpi=args.dpi,
                )
            )
        elif args.command == "chunk":
            _print(_chunk(settings, args.max_chars, args.overlap_chars))
        elif args.command == "report":
            _print(build_quality_report(settings))
        elif args.command == "embed":
            _print(
                generate_embeddings(
                    settings,
                    model_name=args.model,
                    batch_size=args.batch_size,
                    device=args.device,
                )
            )
        elif args.command == "milvus-create":
            with MilvusSink(_milvus_config(args)) as sink:
                _print(_compact_milvus(sink.create_collection(args.dimension)))
        elif args.command == "milvus-ingest":
            chunks, vectors, manifest = load_embedding_artifact(settings)
            with MilvusSink(_milvus_config(args)) as sink:
                result = sink.insert_artifact(
                    chunks,
                    vectors,
                    batch_size=args.batch_size,
                )
            _print(
                {
                    "embedding_manifest": manifest,
                    **_compact_milvus(result),
                    "inserted": result["inserted"],
                }
            )
        elif args.command == "milvus-sync":
            chunks, vectors, manifest = load_embedding_artifact(settings)
            with MilvusSink(_milvus_config(args)) as sink:
                result = sink.sync_artifact(
                    chunks,
                    vectors,
                    batch_size=args.batch_size,
                )
            _print(
                {
                    "embedding_manifest": manifest,
                    **_compact_milvus(result),
                    "existing": result["existing"],
                    "missing": result["missing"],
                    "inserted": result["inserted"],
                }
            )
        elif args.command == "milvus-check":
            with MilvusSink(_milvus_config(args)) as sink:
                _print(_compact_milvus(sink.describe()))
        elif args.command == "run":
            items, total = fetch_inventory(settings)
            _print({"stage": "inventory", "total": total, "written": len(items)})
            _print({"stage": "crawl-details", **crawl_details(settings)})
            _print({"stage": "parse-pages", **parse_documents(settings)})
            if not args.skip_attachments:
                _print(
                    {
                        "stage": "crawl-attachments",
                        **crawl_attachments(settings),
                    }
                )
                _print(
                    {"stage": "parse-attachments", **parse_documents(settings)}
                )
            _print(
                {
                    "stage": "chunk",
                    **_chunk(
                        settings, args.max_chars, args.overlap_chars
                    ),
                }
            )
            _print({"stage": "report", **build_quality_report(settings)})
        return 0
    except (KeyboardInterrupt, BrokenPipeError):
        return 130
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
