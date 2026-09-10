"""POSIX, local-filesystem managed store boundary. No dotenv or app imports.

The persistent lock file is never unlinked: flock is a cooperative process lease,
not a distributed lock or protection against another program running as our UID.
"""

from __future__ import annotations

import json
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

DATABASE_NAME = "agent.db"
STORE_MARKER = "agent-store.json"
PENDING_MARKER = ".agent-store-reserved"
LOCK_NAME = "agent-store.lock"


class StorageError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def absolute_path(value: str | Path) -> Path:
    if os.name != "posix":
        raise StorageError("unsupported_platform", "受控 SQLite 当前仅支持 POSIX 本地文件系统")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or any(ord(c) < 32 for c in str(path)):
        raise StorageError("invalid_path", "必须提供无上级跳转的绝对路径")
    # Do not resolve an untrusted symlink into an apparently safe target.
    for component in (path, *path.parents):
        if component.is_symlink():
            raise StorageError("unsafe_path", "存储路径不能包含符号链接")
    if path in {Path("/"), Path.home(), Path.cwd()}:
        raise StorageError("unsafe_path", "请使用独立的专用存储子目录")
    return path


def private_directory(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise StorageError("unsafe_permissions", "存储目录必须由当前运行 UID 持有且权限为 0700")


def private_file(path: Path) -> None:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
        or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise StorageError("unsafe_permissions", "存储文件必须为当前 UID 的 0600 普通文件，不能使用链接")


def create_private_file(path: Path, data: bytes = b"") -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def write_json(path: Path, data: dict) -> None:
    create_private_file(path, (json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n").encode())


def read_json(path: Path) -> dict:
    private_file(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as source:
        raw = source.read(16385)
    if len(raw) > 16384:
        raise StorageError("invalid_manifest", "存储清单超出大小限制")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise StorageError("invalid_manifest", "存储清单无效") from None
    if not isinstance(value, dict):
        raise StorageError("invalid_manifest", "存储清单无效")
    return value


def fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def reserve_store(directory: str | Path) -> Path:
    directory = absolute_path(directory)
    # No parents=True and no exist_ok: never adopt or replace an existing target.
    directory.mkdir(mode=0o700)
    create_private_file(directory / PENDING_MARKER)
    create_private_file(directory / LOCK_NAME)
    create_private_file(directory / DATABASE_NAME)
    fsync_directory(directory)
    return directory


def publish_store(directory: Path) -> None:
    write_json(directory / STORE_MARKER, {
        "format_version": 1, "database": DATABASE_NAME, "store_id": str(uuid4()),
    })
    # A successful manifest is the commit marker. Keep the empty reservation file;
    # neither marker contains PII, credentials or mutable runtime state.
    fsync_directory(directory)
    fsync_directory(directory.parent)


def validate_store(directory: str | Path) -> tuple[Path, dict]:
    directory = absolute_path(directory)
    private_directory(directory)
    value = read_json(directory / STORE_MARKER)
    if set(value) != {"format_version", "database", "store_id"} or type(value["format_version"]) is not int or value["format_version"] != 1 or value["database"] != DATABASE_NAME:
        raise StorageError("incompatible_store", "受控存储版本或数据库名称不受支持")
    try:
        if str(UUID(value["store_id"])) != value["store_id"]:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise StorageError("invalid_manifest", "受控存储标识无效") from None
    for name in (DATABASE_NAME, LOCK_NAME):
        private_file(directory / name)
    for suffix in ("-wal", "-shm", "-journal"):
        candidate = directory / (DATABASE_NAME + suffix)
        if os.path.lexists(candidate):
            private_file(candidate)
    return directory, value


def init_store(directory: str | Path, *, if_absent: bool = False) -> Path:
    directory = absolute_path(directory)
    if if_absent and os.path.lexists(directory):
        validate_store(directory)
        return directory
    reserve_store(directory)
    publish_store(directory)
    return directory


@contextmanager
def store_lease(directory: str | Path):
    try:
        import fcntl
    except ImportError:
        raise StorageError("unsupported_platform", "受控 SQLite 当前仅支持 POSIX 本地文件系统") from None
    directory, marker = validate_store(directory)
    fd = os.open(directory / LOCK_NAME, os.O_RDWR | os.O_NOFOLLOW)
    try:
        current = os.fstat(fd)
        expected = (directory / LOCK_NAME).lstat()
        if (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino):
            raise StorageError("unsafe_path", "存储锁文件发生变化")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise StorageError("store_busy", "存储正在使用；请停止服务并等待退出后再操作") from None
        validate_store(directory)
        yield directory, marker
    finally:
        os.close(fd)


@contextmanager
def runtime_store_guard(database_path: str | Path, *, required: bool = False):
    path = Path(database_path)
    marked = any(os.path.lexists(path.parent / name) for name in (STORE_MARKER, PENDING_MARKER))
    if not required and not marked:
        yield
        return
    path = absolute_path(path)
    if path.name != DATABASE_NAME:
        raise StorageError("invalid_database_name", "受控存储必须使用目录内固定的 agent.db")
    with store_lease(path.parent):
        yield
