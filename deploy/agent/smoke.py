"""Finite LOCAL Docker HTTPS/recovery/rollback drill; synthetic business data only.

No .env, credentials from the repository, remote deployment or registry push.
Containers/networks are removed on exit; private TLS artifacts and named volumes
are deliberately retained. Run with the workspace virtualenv (httpx required).
"""

import argparse
import json
import os
import re
import socket
import ssl
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[2]
PROXY_KEY = "synthetic-deployment-proxy-6a3"
SIGNING_KEY = "synthetic-deployment-signing-6a3-not-a-production-secret"


def require(condition, label):
    if not condition:
        raise RuntimeError("Synthetic deployment check failed: " + label)


def ci_error(error):
    """Expose bounded synthetic-only failures in the public CI annotations."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    message = str(error)[-6000:]
    for value, replacement in (("%", "%25"), ("\r", "%0D"), ("\n", "%0A")):
        message = message.replace(value, replacement)
    print("::error title=Synthetic deployment failed::" + message, flush=True)


class Drill:
    def __init__(self, *, port=0, transport_mode="https"):
        require(transport_mode in {"https", "private-http"}, "explicit transport mode")
        self.transport_mode = transport_mode
        endpoint = os.environ.get("DOCKER_HOST")
        if not endpoint:
            endpoint = subprocess.run(
                ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
                check=True, text=True, capture_output=True, timeout=10,
            ).stdout.strip()
        if not endpoint.startswith("unix://"):
            raise ValueError("This drill does not operate a remote Docker host")
        # Pin the checked endpoint: ambient DOCKER_CONTEXT must not redirect later calls.
        self.docker = ["docker", "--host", endpoint]
        if port == 0:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
        require(1024 <= port <= 65535, "loopback port")
        self.directory = Path(tempfile.mkdtemp(prefix="spb-agent-6a3-")).resolve()
        self.project = "spb-agent-6a3-" + uuid4().hex[:12]
        scheme = "https" if transport_mode == "https" else "http"
        self.origin = f"{scheme}://127.0.0.1:{port}"
        tls = self.directory / "tls"
        tls.mkdir(mode=0o700)
        cert = tls / "server.crt"
        key = tls / "server.key"
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256", "-days", "1",
            "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
            "-keyout", str(key), "-out", str(cert),
        ], check=True, capture_output=True, timeout=30)
        key.chmod(0o600)
        cert.chmod(0o600)
        self.context = ssl.create_default_context(cafile=str(cert))
        self.env = dict(os.environ, **{
            "AGENT_PUBLIC_ORIGIN": self.origin, "AGENT_HTTPS_PORT": str(port),
            "AGENT_TLS_DIRECTORY": str(tls), "AGENT_PROXY_API_KEY": PROXY_KEY,
            "AGENT_BROWSER_SIGNING_KEY": SIGNING_KEY, "AGENT_BROWSER_PREVIOUS_SIGNING_KEY": "",
            "AGENT_RAG_BASE_URL": "", "AGENT_RAG_API_KEY": "", "AGENT_MYSQL_DSN": "",
        })
        self.env.pop("DOCKER_CONTEXT", None)
        self.base = [*self.docker, "compose", "--env-file", "/dev/null", "--project-name", self.project]
        self.files = ["docker-compose.yml", "docker-compose.synthetic.yml"]
        if transport_mode == "private-http":
            self.files.append("docker-compose.synthetic-private-http.yml")
        self.report = {"project": self.project, "origin": self.origin, "business_network_calls": 0, "checks": []}

    def inventory(self, *args):
        return subprocess.run(
            [*self.docker, *args], cwd=ROOT, env=self.env, text=True,
            capture_output=True, timeout=15, check=True,
        ).stdout.strip()

    def compose(self, *args, extra=(), timeout=100, success=True):
        command = self.base.copy()
        for filename in (*self.files, *extra):
            command += ["-f", str(ROOT / "deploy/agent" / filename)]
        result = subprocess.run(command + list(args), cwd=ROOT, env=self.env, text=True, capture_output=True, timeout=timeout)
        if success and result.returncode:
            raise RuntimeError("Synthetic compose failed: " + " ".join(args[:3]) + "\n" + result.stderr[-2500:])
        return result

    def client(self):
        return httpx.Client(base_url=self.origin, verify=self.context, trust_env=False, timeout=15, headers={
            "Origin": self.origin, "Sec-Fetch-Site": "same-origin",
        })

    def check(self, name):
        self.report["checks"].append(name)
        print("PASS " + name, flush=True)

    def bootstrap(self, client):
        response = client.post("/api/v2/agent/browser-session", json={})
        require(response.status_code == 200, "proxy role matches API key")
        client.headers["X-Agent-Session"] = response.json()["session_ref"]
        return response

    def send(self, client, message, key, conversation=None, stream=False):
        response = client.post("/api/v2/agent/messages", json={
            "message": message, "conversation_id": conversation, "stream": stream,
        }, headers={"Idempotency-Key": key})
        require(response.status_code == 200, "message HTTP status")
        if not stream:
            return response.json()
        require("text/event-stream" in response.headers["content-type"], "SSE content type")
        events = [(block.splitlines()[0][7:], json.loads(block.splitlines()[1][6:])) for block in response.text.strip().split("\n\n")]
        require(events[-1][0] == "done" and any(name == "result" for name, _ in events), "SSE terminal/result contract")
        return events[-1][1]["response"]

    def execution_nodes(self, *, extra=()):
        # Internal aggregate telemetry only; /api/metrics remains publicly denied.
        script = (
            "import urllib.request; "
            "data=urllib.request.urlopen('http://127.0.0.1:8081/metrics',timeout=3).read().decode(); "
            "print(sum(float(line.rsplit(' ',1)[1]) for line in data.splitlines() "
            "if line.startswith('assistant_agent_node_executions_total{') and 'node=\"execute_tool\"' in line))"
        )
        return float(self.compose("exec", "-T", "assistant-api", "/app/.venv/bin/python", "-c", script, extra=extra).stdout.strip())

    def run(self, *, build=False):
        restored = ("docker-compose.restored.yml",)
        rollback = (*restored, "docker-compose.legacy.yml")
        try:
            self.compose("config", "--quiet")
            if build:
                self.compose("build", "assistant-api", "chat-web", timeout=900)
                self.compose("build", "chat-web", extra=("docker-compose.legacy.yml",), timeout=900)
            self.report["images"] = {
                name: self.inventory("image", "inspect", name, "--format", "{{.Id}} {{.Os}}/{{.Architecture}}")
                for name in ("intern26-spb-agent-api:6a3", "intern26-spb-agent-web:6a3", "intern26-spb-agent-web-legacy:6a3")
            }
            self.compose("up", "-d", "--wait", "--wait-timeout", "75", timeout=110)
            with self.client() as first, self.client() as second:
                page = first.get("/")
                require(page.status_code == 200 and "frame-ancestors 'none'" in page.headers.get("content-security-policy", ""), "HTTPS / CSP")
                require(first.get("/", headers={"Host": "untrusted.invalid"}).status_code == 421, "Host allowlist")
                denied = ["/api/metrics", "/api/health/ready", "/api/v2/agent/health/ready", "/api/docs", "/api/openapi.json", "/api/v1/chat", "/api/v2/agent/messages/", "/.env", "/docs", "/api/v2/agent/../../metrics"]
                require(all(first.get(path).status_code == 404 for path in denied), "public route allowlist")
                require(first.get("/api/v2/agent/messages").status_code == 405, "method allowlist")
                require(first.post("/api/v2/agent/browser-session", json={}, headers={"Origin": "https://untrusted.invalid"}).status_code == 403, "Origin rejection")
                self.check(self.transport_mode + "-host-origin-public-route-boundary")

                identity = self.bootstrap(first)
                cookie = identity.headers.get("set-cookie", "")
                require(all(value in cookie for value in ("HttpOnly", "SameSite=strict", "Path=/")), "cookie flags")
                if self.transport_mode == "https":
                    require("__Host-spb-agent=" in cookie and "Secure" in cookie, "HTTPS secure cookie")
                else:
                    require("spb-agent-intranet=" in cookie and "Secure" not in cookie and "Domain=" not in cookie, "explicit private HTTP cookie")
                self.bootstrap(second)
                require(first.headers["X-Agent-Session"] != second.headers["X-Agent-Session"], "two visitors")
                for asset in re.findall(r'(?:src|href)="(/assets/[^\"]+)"', page.text):
                    content = first.get(asset)
                    require(content.status_code == 200 and PROXY_KEY not in content.text and SIGNING_KEY not in content.text, "no server keys in assets")
                pending = self.send(first, "查邮件轨迹", "pending-create")
                require(pending["phase"] == "waiting_user", "persistent interrupt")
                done = self.send(first, "查邮件轨迹 1234567890123", "done-create")
                require(done["phase"] == "completed" and self.execution_nodes() == 1, "one initial tool execution")
                spoof = second.post("/api/v2/agent/messages", json={"message": "1234567890123", "conversation_id": pending["conversation_id"]}, headers={
                    "Idempotency-Key": "cross-owner", "Authorization": "Bearer forged", "X-API-Key": "forged",
                    "X-Agent-Owner": first.headers["X-Agent-Session"], "X-Forwarded-User": first.headers["X-Agent-Session"],
                })
                require(spoof.status_code == 404, "cross-owner / forged identity")
                self.check(self.transport_mode + "-cookie-json-two-visitors-and-no-client-secrets")

                busy = self.compose("run", "--rm", "--no-deps", "storage", "-m", "spb_assistant_api.storage_cli", "backup", "--source", "/var/lib/spb-runtime/store", "--destination", "/var/lib/spb-backups/busy", success=False)
                require(busy.returncode == 2 and '"store_busy"' in busy.stderr, "online backup rejected")
                self.compose("stop", "chat-web", "assistant-api")
                self.compose("run", "--rm", "--no-deps", "storage", "-m", "spb_assistant_api.storage_cli", "backup", "--source", "/var/lib/spb-runtime/store", "--destination", "/var/lib/spb-backups/snapshot")
                self.compose("run", "--rm", "--no-deps", "storage", "-m", "spb_assistant_api.storage_cli", "verify", "--source", "/var/lib/spb-backups/snapshot")
                self.compose("run", "--rm", "--no-deps", "storage", "-m", "spb_assistant_api.storage_cli", "restore", "--source", "/var/lib/spb-backups/snapshot", "--destination", "/var/lib/spb-restored/store")
                self.compose("up", "-d", "--wait", "--wait-timeout", "75", extra=restored, timeout=110)
                self.bootstrap(first)
                replay = self.send(first, "查邮件轨迹 1234567890123", "done-create")
                require(replay["result"] == done["result"] and self.execution_nodes(extra=restored) == 0, "restored completed replay")
                resumed = self.send(first, "1234567890123", "pending-finish", pending["conversation_id"], stream=True)
                require(resumed["phase"] == "completed" and self.execution_nodes(extra=restored) == 1, "restored SSE resume")
                again = self.send(first, "1234567890123", "pending-finish", pending["conversation_id"], stream=True)
                require(again["result"] == resumed["result"] and self.execution_nodes(extra=restored) == 1, "SSE replay performs no new node")
                self.check("quiescent-backup-new-volume-restore-sse-resume-and-replay")

                self.compose("stop", "chat-web", "assistant-api", extra=restored)
                self.compose("up", "-d", "--wait", "--wait-timeout", "75", extra=rollback, timeout=110)
                require(first.get("/").status_code == 200, "legacy web")
                require(first.post("/api/v2/agent/browser-session", json={}).status_code == 404, "legacy excludes V2")
                legacy = first.post("/api/v1/chat", json={"mode": "policy", "question": "部署演练", "stream": False})
                require(legacy.status_code == 200 and "合成演练" in legacy.text, "legacy JSON tool contract")
                stream = first.post("/api/v1/chat", json={"mode": "policy", "question": "部署演练", "stream": True})
                require(stream.status_code == 200 and "event: done" in stream.text, "legacy SSE contract")
                self.compose("stop", "chat-web", "assistant-api", extra=rollback)
                self.compose("up", "-d", "--wait", "--wait-timeout", "75", extra=restored, timeout=110)
                self.bootstrap(first)
                replay = self.send(first, "1234567890123", "pending-finish", pending["conversation_id"], stream=True)
                require(replay["result"] == resumed["result"] and self.execution_nodes(extra=restored) == 0, "Agent survives V1 rollback")
                require(first.delete("/api/v2/agent/conversations/" + done["conversation_id"]).status_code == 204, "delete route")
                self.check("v1-json-sse-rollback-and-return-without-schema-downgrade")
            self.report["status"] = "passed"
        finally:
            self.report.setdefault("status", "failed")
            if self.report.get("status") != "passed":
                try:
                    diagnostics = self.compose("logs", "--no-color", "--tail", "60", extra=rollback, success=False)
                    (self.directory / "synthetic-diagnostics.log").write_text(diagnostics.stdout + diagnostics.stderr)
                except (OSError, subprocess.TimeoutExpired):
                    pass  # Diagnostics must not prevent cleanup of this unique project.
            stopped = self.compose("down", "--timeout", "45", extra=rollback, timeout=100, success=False)
            self.report["containers_removed"] = stopped.returncode == 0
            try:
                self.report["retained_volumes"] = self.inventory(
                    "volume", "ls", "--filter", "label=com.docker.compose.project=" + self.project,
                    "--format", "{{.Name}}",
                ).splitlines()
            except (OSError, subprocess.SubprocessError):
                self.report["volume_inventory_unavailable"] = True
            (self.directory / "report.json").write_text(json.dumps(self.report, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps({"project": self.project, "report": str(self.directory / "report.json"), "containers_removed": stopped.returncode == 0}), flush=True)
            require(stopped.returncode == 0, "cleanup (volumes are retained)")
        return self.report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--transport", choices=("https", "private-http"), default="https")
    args = parser.parse_args()
    try:
        Drill(port=args.port, transport_mode=args.transport).run(build=args.build)
    except Exception as error:
        ci_error(error)
        raise  # Diagnostics never turn a failed gate into success.
