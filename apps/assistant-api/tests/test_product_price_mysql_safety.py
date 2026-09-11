"""Exercise test-target guards without starting Docker or opening any database."""

from __future__ import annotations

import importlib.util
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.engine import URL, make_url

from .product_price_mysql_fixture import guarded_test_dsns


RUN_ID = "a1" * 10
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def synthetic_test_env(monkeypatch):
    for key in tuple(os.environ):
        if key.startswith(("PRICE_QUERY_TEST_", "RUN_PRODUCT_PRICE_", "DOCKER_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("RUN_PRODUCT_PRICE_MYSQL_TESTS", "1")
    monkeypatch.setenv("PRICE_QUERY_TEST_RUN_ID", RUN_ID)
    for role in ("writer", "reader"):
        value = URL.create(
            "mysql+pymysql", username=f"price_{role}", password=f"synthetic-{role}",
            host="127.0.0.1", port=43306, database=f"price_query_test_{RUN_ID}",
            query={"charset": "utf8mb4"},
        ).render_as_string(hide_password=False)
        monkeypatch.setenv(f"PRICE_QUERY_TEST_{role.upper()}_DSN", value)


def test_sql_fixture_requires_explicit_opt_in(synthetic_test_env, monkeypatch):
    monkeypatch.delenv("RUN_PRODUCT_PRICE_MYSQL_TESTS")
    with pytest.raises(pytest.skip.Exception):
        guarded_test_dsns()


def test_sql_fixture_accepts_only_the_current_run_pair(synthetic_test_env):
    writer, reader = map(make_url, guarded_test_dsns())
    assert writer.database == reader.database == f"price_query_test_{RUN_ID}"
    assert writer.password != reader.password


@pytest.mark.parametrize("changes", [
    {"host": "10.0.0.1"},
    {"host": "localhost"},
    {"database": "device_price"},
    {"database": "price_query_test_b123456789b1234567890"},
    {"username": "root"},
    {"username": "price_reader"},
    {"drivername": "mysql"},
    {"port": 3306},  # Different target from reader, even on loopback.
    {"port": 443},
    {"query": {"charset": "utf8mb4", "unix_socket": "/tmp/another-mysql.sock"}},
    {"password": "synthetic-reader"},
])
def test_sql_fixture_rejects_target_drift_before_connecting(synthetic_test_env, monkeypatch, changes):
    key = "PRICE_QUERY_TEST_WRITER_DSN"
    url = make_url(os.environ[key]).set(**changes)
    monkeypatch.setenv(key, url.render_as_string(hide_password=False))
    with pytest.raises(ValueError, match="only this run's isolated loopback") as error:
        guarded_test_dsns()
    assert "synthetic-writer" not in str(error.value)
    assert "synthetic-reader" not in str(error.value)


@pytest.mark.parametrize("token", ["", "../private", "A" * 20, "a" * 19, "a" * 21])
def test_sql_fixture_rejects_missing_or_invalid_run_identity(synthetic_test_env, monkeypatch, token):
    monkeypatch.setenv("PRICE_QUERY_TEST_RUN_ID", token)
    with pytest.raises(ValueError, match="runner token"):
        guarded_test_dsns()


@pytest.fixture
def mysql_runner(synthetic_test_env):
    spec = importlib.util.spec_from_file_location(
        "synthetic_product_price_mysql_runner", ROOT / "deploy/price-query/mysql_smoke.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("endpoint", ["tcp://127.0.0.1:2375", "tcp://10.0.0.1:2375", "ssh://server"])
def test_runner_rejects_remote_host_before_running_docker(mysql_runner, monkeypatch, endpoint):
    monkeypatch.setenv("DOCKER_HOST", endpoint)
    monkeypatch.setattr(mysql_runner.subprocess, "run", lambda *args, **kwargs: pytest.fail("must not run Docker"))
    with pytest.raises(RuntimeError, match="Remote DOCKER_HOST"):
        mysql_runner.local_docker_endpoint()


@pytest.mark.parametrize("endpoint", ["ssh://server", "tcp://127.0.0.1:2375", "unix:///tmp/socket?remote=1"])
def test_runner_rejects_unsafe_context_result(mysql_runner, monkeypatch, endpoint):
    monkeypatch.setattr(mysql_runner.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout=endpoint,
    ))
    with pytest.raises(RuntimeError, match="Only a local Unix"):
        mysql_runner.local_docker_endpoint()


def test_runner_pins_validated_socket_and_removes_later_context_override(mysql_runner, monkeypatch):
    endpoint = "unix:///tmp/synthetic-docker.sock"
    monkeypatch.setenv("DOCKER_HOST", endpoint)
    monkeypatch.setattr(mysql_runner.Path, "stat", lambda self: SimpleNamespace(st_mode=stat.S_IFSOCK))
    mysql_runner.DOCKER_ENDPOINT = mysql_runner.local_docker_endpoint()
    monkeypatch.setenv("DOCKER_CONTEXT", "later-remote-context")
    monkeypatch.setenv("DOCKER_HOST", "ssh://later-remote-host")

    def run(arguments, **kwargs):
        assert arguments == ["docker", "--host", endpoint, "version"]
        assert not any(key.startswith("DOCKER_") for key in kwargs["env"])
        return SimpleNamespace(returncode=0, stdout="synthetic-ok")

    monkeypatch.setattr(mysql_runner.subprocess, "run", run)
    assert mysql_runner.docker("version") == "synthetic-ok"


def test_runner_cannot_invoke_docker_before_endpoint_validation(mysql_runner):
    with pytest.raises(RuntimeError, match="validated first"):
        mysql_runner.docker("version")
