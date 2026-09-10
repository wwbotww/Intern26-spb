"""Verify pinned bases against the registry, independent of the local image cache.

Never authenticate, pull layers, change pins or fall back to mutable tags. When
a pin is unavailable, report the official source tag's current digest for an
operator to review; the gate still fails. Only allowlisted references / digests
are emitted to public CI annotations, not command output or environment values.
"""

import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCES = (
    ("Dockerfile.api", "ghcr.io/astral-sh/uv", "python3.12-bookworm-slim"),
    ("Dockerfile.web", "node", "22.23.2-alpine"),
    ("Dockerfile.web", "nginx", "1.31.3-alpine"),
)
DIGEST = re.compile(r"^Digest:\s+(sha256:[0-9a-f]{64})$", re.MULTILINE)


def inspect(reference):
    try:
        result = subprocess.run(
            ["docker", "buildx", "imagetools", "inspect", reference],
            capture_output=True, text=True, timeout=45,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = DIGEST.search(result.stdout) if result.returncode == 0 else None
    return match.group(1) if match else None


def verify():
    valid = True
    for filename, repository, tag in SOURCES:
        match = re.search(re.escape(repository) + r"@(sha256:[0-9a-f]{64})", (ROOT / filename).read_text())
        if not match:
            raise ValueError("Base image must have an explicit registry digest")
        reference = repository + "@" + match.group(1)
        found = inspect(reference)
        if found == match.group(1):
            print("PASS registry manifest " + reference, flush=True)
            continue
        valid = False
        candidate = inspect(repository + ":" + tag)
        message = "Unavailable pinned manifest: " + reference
        if candidate:
            message += "; review source " + repository + ":" + tag + "@" + candidate
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print("::error title=Base image registry verification::" + message, flush=True)
        else:
            print(message, flush=True)
    return valid


if __name__ == "__main__":
    raise SystemExit(0 if verify() else 1)
