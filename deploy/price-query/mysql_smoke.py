"""Run synthetic V2-only SQL checks in a new disposable local MySQL container.

This does not load dotenv, connect to a supplied database, use existing containers,
or mount a host data directory. Only the two reviewed image digests are accepted.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pymysql
from sqlalchemy.engine import URL


ROOT = Path(__file__).resolve().parents[2]
IMAGES = {
    "5.7.36": "mysql@sha256:f2ad209efe9c67104167fc609cca6973c8422939491c9345270175a300419f94",
    "8.4": "mysql@sha256:b3b90af2a6552ae30c266fdb7d5dd55f3afb72404bb78d37fe8a23eb857fd3fb",
}
LABEL = "intern26.synthetic-price-query"
DOCKER_ENDPOINT: str | None = None


def local_docker_endpoint() -> str:
    """Resolve once, reject TCP/SSH contexts, then pin every Docker invocation."""
    context = os.environ.get("DOCKER_CONTEXT")
    explicit_host = os.environ.get("DOCKER_HOST")
    if explicit_host and not explicit_host.startswith("unix:///"):
        raise RuntimeError("Remote DOCKER_HOST is forbidden for the synthetic runner")
    if explicit_host and not context:
        endpoint = explicit_host
    else:
        arguments = ["docker", "context", "inspect"]
        if context:
            arguments.append(context)
        arguments += ["--format", "{{.Endpoints.docker.Host}}"]
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise RuntimeError("Could not resolve the local Docker context")
        endpoint = result.stdout.strip()
    if not endpoint.startswith("unix:///") or any(char in endpoint for char in "\r\n?#"):
        raise RuntimeError("Only a local Unix Docker socket is accepted")
    socket_file = Path(endpoint.removeprefix("unix://"))
    if not stat.S_ISSOCK(socket_file.stat().st_mode):
        raise RuntimeError("Local Docker endpoint is not a Unix socket")
    return endpoint


def docker(*arguments: str, timeout: int = 45) -> str:
    if DOCKER_ENDPOINT is None:
        raise RuntimeError("Local Docker endpoint must be validated first")
    docker_env = {key: value for key, value in os.environ.items() if not key.startswith("DOCKER_")}
    result = subprocess.run(
        ["docker", "--host", DOCKER_ENDPOINT, *arguments], capture_output=True, text=True,
        timeout=timeout, env=docker_env,
    )
    if result.returncode:
        # Docker output may contain environment values. Keep it out of public CI.
        raise RuntimeError(f"Docker {arguments[0]} failed; inspect the synthetic container locally")
    return result.stdout.strip()


def wait_for_mysql(port: int, password: str, seconds: int = 180):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            return pymysql.connect(
                host="127.0.0.1", port=port, user="root", password=password,
                connect_timeout=2, read_timeout=5, write_timeout=5, autocommit=True,
                charset="utf8mb4",
            )
        except pymysql.MySQLError:
            time.sleep(1)
    raise RuntimeError("Synthetic MySQL did not become ready within the startup budget")


def dsn(port: int, database: str, username: str, password: str) -> str:
    return URL.create(
        "mysql+pymysql", username=username, password=password, host="127.0.0.1",
        port=port, database=database, query={"charset": "utf8mb4"},
    ).render_as_string(hide_password=False)


def main() -> int:
    global DOCKER_ENDPOINT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mysql", choices=IMAGES, required=True)
    parser.add_argument(
        "--pull", action="store_true",
        help="Allow pulling only the selected pinned image when not already cached (CI)",
    )
    parser.add_argument("--python", default=sys.executable, help="Installed test interpreter")
    args = parser.parse_args()
    image = IMAGES[args.mysql]
    token = secrets.token_hex(10)
    container_name = f"price-query-test-{token}"
    database = f"price_query_test_{token}"
    passwords = {user: secrets.token_urlsafe(24) for user in ("root", "writer", "reader")}
    container_id: str | None = None
    try:
        DOCKER_ENDPOINT = local_docker_endpoint()
        try:
            docker("image", "inspect", image)
        except RuntimeError:
            if not args.pull:
                raise RuntimeError("Pinned MySQL image is not cached; use --pull to fetch this exact digest") from None
            docker("pull", image, timeout=600)
        with tempfile.TemporaryDirectory(prefix="price-query-smoke-") as scratch:
            env_file = Path(scratch) / "mysql.env"
            env_file.write_text(
                f"MYSQL_ROOT_PASSWORD={passwords['root']}\nMYSQL_ROOT_HOST=%\n", encoding="utf-8",
            )
            env_file.chmod(0o600)
            arguments = [
                "run", "--detach", "--pull=never", "--name", container_name,
                "--label", f"{LABEL}={token}", "--publish", "127.0.0.1::3306",
                "--tmpfs", "/var/lib/mysql:rw,nosuid,size=1024m", "--env-file", str(env_file),
                image,
            ]
            # This compatibility test intentionally covers both supported SQL
            # versions, not production authentication-provider configuration.
            if args.mysql == "8.4":
                # Merely enabling the plugin leaves root on caching_sha2,
                # which would silently require the unselected cryptography
                # extra during bootstrap. Pin the test account policy too.
                arguments += [
                    "--mysql-native-password=ON",
                    "--authentication-policy=mysql_native_password",
                ]
            container_id = docker(*arguments)
            if not re.fullmatch(r"[0-9a-f]{64}", container_id):
                raise RuntimeError("Unexpected synthetic container identifier")
            binding = docker("port", container_id, "3306/tcp")
            match = re.fullmatch(r"127\.0\.0\.1:(\d+)", binding)
            if not match:
                raise RuntimeError("Synthetic MySQL must publish exactly one loopback binding")
            port = int(match.group(1))
            with wait_for_mysql(port, passwords["root"]) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
                    for user in ("writer", "reader"):
                        cursor.execute(
                            f"CREATE USER 'price_{user}'@'%%' IDENTIFIED WITH mysql_native_password BY %s",
                            (passwords[user],),
                        )
                    cursor.execute(f"GRANT ALL PRIVILEGES ON `{database}`.* TO 'price_writer'@'%'")
                    # The writer creates the exact v2 table projection first;
                    # table-level reader grants deliberately exclude old tables.
                    schema = (ROOT / "apps/assistant-api/tests/fixtures/product_price/mysql_schema.sql").read_text()
                    with pymysql.connect(
                        host="127.0.0.1", port=port, user="price_writer", password=passwords["writer"],
                        database=database, autocommit=True, charset="utf8mb4",
                    ) as writer_connection:
                        with writer_connection.cursor() as writer_cursor:
                            for statement in schema.split(";"):
                                if statement.strip():
                                    writer_cursor.execute(statement)
                    for table in re.findall(r"CREATE TABLE (v2_[a-z_]+)\s*\(", schema):
                        cursor.execute(f"GRANT SELECT ON `{database}`.`{table}` TO 'price_reader'@'%'")
                    cursor.execute("SELECT VERSION()")
                    version = str(cursor.fetchone()[0])
                    if not (version == "5.7.36" if args.mysql == "5.7.36" else version.startswith("8.4.")):
                        raise RuntimeError("Pinned image unexpectedly reports a different MySQL version")
            env = {
                key: value for key, value in os.environ.items()
                if not key.upper().startswith(("ASSISTANT_", "PRICE_QUERY_TEST_"))
            }
            env.update({
                "RUN_PRODUCT_PRICE_MYSQL_TESTS": "1",
                "PRICE_QUERY_TEST_RUN_ID": token,
                "PRICE_QUERY_TEST_WRITER_DSN": dsn(port, database, "price_writer", passwords["writer"]),
                "PRICE_QUERY_TEST_READER_DSN": dsn(port, database, "price_reader", passwords["reader"]),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(ROOT / "apps/assistant-api/src"),
            })
            print(f"Synthetic MySQL {version}: V2-only and denied-V1 gates", flush=True)
            result = subprocess.run([
                args.python, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                "apps/assistant-api/tests/test_product_price_mysql_integration.py",
                "--tb=short", f"--basetemp={scratch}/pytest",
            ], cwd=ROOT, env=env, timeout=300)
            return result.returncode
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired, pymysql.MySQLError) as exc:
        # Do not include exception arguments from DB clients or environment.
        message = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        print(f"Synthetic price-query smoke failed: {message}", file=sys.stderr)
        return 1
    finally:
        if container_id and re.fullmatch(r"[0-9a-f]{64}", container_id):
            actual_label = docker("inspect", "--format", '{{index .Config.Labels "' + LABEL + '"}}', container_id)
            if actual_label == token:
                docker("rm", "--force", container_id)
                print("Removed this run's synthetic container and its tmpfs test data", flush=True)
            else:
                raise RuntimeError("Cleanup refused: synthetic container ownership label differs")


if __name__ == "__main__":
    raise SystemExit(main())
