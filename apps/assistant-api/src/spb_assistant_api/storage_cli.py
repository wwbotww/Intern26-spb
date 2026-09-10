"""Operator-only managed SQLite init/backup/verify/restore. Never loads .env."""

import argparse
import json
import sqlite3
import sys

from .storage_paths import StorageError, init_store
from .storage_snapshot import backup_store, restore_backup, verify_backup


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create a new private managed data directory")
    init.add_argument("--directory", required=True)
    init.add_argument("--if-absent", action="store_true", help="Validate an existing managed store, never adopt an unmanaged directory")
    for name in ("backup", "verify", "restore"):
        command = commands.add_parser(name)
        command.add_argument("--source", required=True)
        if name != "verify":
            command.add_argument("--destination", required=True, help="A new directory; overwrite is never supported")
        command.add_argument("--timeout-seconds", type=float, default=30)
        command.add_argument("--max-mib", type=int, default=256)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            init_store(args.directory, if_absent=args.if_absent)
            report = {"status": "ok", "operation": "init"}
        else:
            options = {"timeout": args.timeout_seconds, "max_bytes": args.max_mib * 1024**2}
            if args.command == "backup":
                result = backup_store(args.source, args.destination, **options)
            elif args.command == "restore":
                result = restore_backup(args.source, args.destination, **options)
            else:
                result = verify_backup(args.source, **options)
            report = {"status": "ok", "operation": args.command, "profile": result["profile"], "size_bytes": result["size_bytes"], "table_rows": result["table_rows"]}
        print(json.dumps(report, sort_keys=True))
        return 0
    except (StorageError, OSError, sqlite3.Error) as error:
        code = error.code if isinstance(error, StorageError) else "target_exists" if isinstance(error, FileExistsError) else "storage_io_failed"
        # No paths, SQL, state payloads or raw exception details in diagnostic output.
        print(json.dumps({"status": "failed", "operation": args.command, "code": code, "message": "操作未完成；已有数据未被替换。不完整的新目录不能用于恢复，请按文档核对。"}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
