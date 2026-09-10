import asyncio
import os
import select
import subprocess
import sys
from pathlib import Path

import pytest

from spb_assistant_api.storage_paths import (
    DATABASE_NAME, LOCK_NAME, STORE_MARKER, StorageError, init_store,
    runtime_store_guard, store_lease, validate_store,
)
from spb_assistant_api.workflow.composition import create_persistent_agent


def test_private_init_idempotent_validation_and_no_adoption(tmp_path):
    store = init_store(tmp_path / "store")
    assert store.stat().st_mode & 0o777 == 0o700
    assert all(item.stat().st_mode & 0o777 == 0o600 for item in store.iterdir())
    before = (store / STORE_MARKER).read_bytes()
    assert init_store(store, if_absent=True) == store
    assert (store / STORE_MARKER).read_bytes() == before
    with pytest.raises(FileExistsError):
        init_store(store)
    empty = tmp_path / "unmanaged"
    empty.mkdir(mode=0o700)
    with pytest.raises(FileNotFoundError):
        init_store(empty, if_absent=True)
    assert list(empty.iterdir()) == []


@pytest.mark.parametrize("kind", ["directory_mode", "db_mode", "lock_mode", "db_symlink", "lock_symlink", "hardlink", "wal_symlink", "marker_symlink"])
def test_unsafe_permissions_or_links_are_rejected_without_repair(tmp_path, kind):
    store = init_store(tmp_path / "store")
    if kind == "directory_mode":
        store.chmod(0o755)
    elif kind in {"db_mode", "lock_mode"}:
        (store / (DATABASE_NAME if kind == "db_mode" else LOCK_NAME)).chmod(0o644)
    elif kind == "hardlink":
        os.link(store / DATABASE_NAME, tmp_path / "hardlink")
    else:
        name = {"db_symlink": DATABASE_NAME, "lock_symlink": LOCK_NAME, "wal_symlink": DATABASE_NAME + "-wal", "marker_symlink": STORE_MARKER}[kind]
        target = store / name
        if target.exists():
            target.rename(store / (name + ".original"))
        target.symlink_to(tmp_path / "unrelated")
    with pytest.raises((StorageError, OSError)):
        validate_store(store)
    assert not (tmp_path / "unrelated").exists()


@pytest.mark.parametrize("kind", ["relative", "parent_jump", "root", "cwd", "home", "missing_parent", "symlink_parent"])
def test_init_refuses_ambiguous_broad_or_missing_parent(tmp_path, kind):
    paths = {"relative": Path("relative-data"), "parent_jump": tmp_path / ".." / "unsafe", "root": Path("/"), "cwd": Path.cwd(), "home": Path.home(), "missing_parent": tmp_path / "missing" / "store"}
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    paths["symlink_parent"] = link / "store"
    with pytest.raises((StorageError, OSError)):
        init_store(paths[kind])


def test_lease_excludes_same_and_other_processes_and_is_not_unlinked(tmp_path):
    store = init_store(tmp_path / "store")
    inode = (store / LOCK_NAME).stat().st_ino
    script = "from spb_assistant_api.storage_paths import store_lease; import sys\nwith store_lease(sys.argv[1]): pass"
    with store_lease(store):
        with pytest.raises(StorageError, match="正在使用"):
            with store_lease(store):
                pass
        child = subprocess.run([sys.executable, "-c", script, str(store)], capture_output=True, text=True, timeout=15)
        assert child.returncode != 0 and "store_busy" not in child.stdout
    child = subprocess.run([sys.executable, "-c", script, str(store)], capture_output=True, text=True, timeout=15)
    assert child.returncode == 0, child.stderr
    assert (store / LOCK_NAME).stat().st_ino == inode


def test_runtime_detects_managed_store_even_without_flag_and_releases_after_exception(tmp_path):
    store = init_store(tmp_path / "store")

    async def scenario():
        with pytest.raises(RuntimeError, match="synthetic"):
            async with create_persistent_agent(database_path=store / DATABASE_NAME) as parts:
                assert await parts.readiness.check() == {"persistence": "ready", "checkpoint": "ready"}
                with pytest.raises(StorageError):
                    with store_lease(store):
                        pass
                raise RuntimeError("synthetic")
        async with create_persistent_agent(database_path=store / DATABASE_NAME, managed_storage=True):
            pass

    asyncio.run(scenario())
    validate_store(store)
    with pytest.raises(StorageError):
        with runtime_store_guard(store / "other.db"):
            pass
    with pytest.raises(FileNotFoundError):
        with runtime_store_guard(tmp_path / DATABASE_NAME, required=True):
            pass


def test_cancelled_runtime_releases_lease(tmp_path):
    store = init_store(tmp_path / "store")

    async def scenario():
        active = asyncio.Event()

        async def run():
            async with create_persistent_agent(database_path=store / DATABASE_NAME):
                active.set()
                await asyncio.Future()

        task = asyncio.create_task(run())
        await active.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with store_lease(store):
            pass

    asyncio.run(scenario())


def test_abrupt_process_exit_releases_lease_without_deleting_lock(tmp_path):
    store = init_store(tmp_path / "store")
    inode = (store / LOCK_NAME).stat().st_ino
    script = "from spb_assistant_api.storage_paths import store_lease; import sys\nwith store_lease(sys.argv[1]):\n print('ready', flush=True)\n sys.stdin.read()"
    child = subprocess.Popen([sys.executable, "-c", script, str(store)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert select.select([child.stdout], [], [], 10)[0]
        assert child.stdout.readline().strip() == "ready"
        with pytest.raises(StorageError):
            with store_lease(store):
                pass
        child.kill()
        child.communicate(timeout=10)
        with store_lease(store):
            pass
        assert (store / LOCK_NAME).stat().st_ino == inode
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)
