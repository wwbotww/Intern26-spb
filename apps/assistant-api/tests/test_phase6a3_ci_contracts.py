"""Read-only toolchain / workflow contracts; no npm, Docker or business network."""

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "apps/chat-web"


def test_vitest_family_uses_one_patched_locked_major_and_explicit_node_contract():
    manifest = json.loads((WEB / "package.json").read_text())
    lock = json.loads((WEB / "package-lock.json").read_text())["packages"]
    version = lock["node_modules/vitest"]["version"]
    parsed = tuple(int(part) for part in version.split("."))
    assert (4, 1, 11) <= parsed < (5, 0, 0)
    assert manifest["devDependencies"] == lock[""]["devDependencies"]
    assert manifest["engines"] == lock[""]["engines"]
    assert manifest["engines"]["node"] == "^22.12.0 || >=24.0.0"
    family = {name: value for name, value in lock.items() if name.startswith("node_modules/@vitest/")}
    assert "node_modules/@vitest/mocker" in family
    assert all(value["version"] == version and value["dev"] for value in family.values())
    assert "node_modules/vite-node" not in lock


def test_default_unit_tests_have_an_isolated_non_serving_configuration():
    manifest = json.loads((WEB / "package.json").read_text())
    assert manifest["scripts"]["test"] == "vitest run"
    config = (WEB / "vitest.config.ts").read_text()
    assert "envDir: false" in config and "include: ['src/**/*.test.ts']" in config
    assert "api: false" in config and "watch: false" in config and "passWithNoTests: false" in config
    assert "loadEnv" not in config and "from './vite.config'" not in config and "proxy:" not in config
    image = (ROOT / "deploy/agent/Dockerfile.web").read_text()
    assert "RUN npm test" in image
    assert "npm ci --include=dev --engine-strict" in image


def test_ci_audits_development_dependencies_and_does_not_suppress_failures():
    manifest = json.loads((WEB / "package.json").read_text())
    script = manifest["scripts"]["audit:dependencies"]
    assert script == "npm audit --include=dev --audit-level=moderate --registry=https://registry.npmjs.org"
    workflow = yaml.safe_load((ROOT / ".github/workflows/agent-ci.yml").read_text())
    steps = workflow["jobs"]["contracts"]["steps"]
    audit = [step for step in steps if step.get("run") == "npm run audit:dependencies"]
    assert len(audit) == 1 and audit[0]["working-directory"] == "apps/chat-web"
    assert not audit[0].get("continue-on-error") and "if" not in audit[0]
    install = next(step["run"] for step in steps if step.get("name") == "Install locked workspace dependencies")
    assert "npm --prefix apps/chat-web ci --include=dev --engine-strict" in install
    web = next(step["run"] for step in steps if step.get("working-directory") == "apps/chat-web" and "npm test" in step.get("run", ""))
    assert "npm test\n" in web and "--config vite.postage-offline.config.ts" not in web.splitlines()[0]
