from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

from spb_assistant_api.domain.intents import Intent
from spb_assistant_api.main import app


WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
ASSISTANT_SOURCE = (
    WORKSPACE_ROOT
    / "apps"
    / "assistant-api"
    / "src"
    / "spb_assistant_api"
)
OPENAPI_DRAFT = (
    WORKSPACE_ROOT
    / "docs"
    / "openapi"
    / "assistant-agent-v2.openapi.json"
)


def _langgraph_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(
                alias.name
                for alias in node.names
                if alias.name.startswith("langgraph")
            )
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("langgraph")
        ):
            imports.append(node.module)
    return imports


def _imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((0, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append((node.level, node.module))
    return imports


def _resolve_local_ref(document: dict[str, Any], ref: str) -> Any:
    assert ref.startswith("#/"), f"只允许当前 artifact 内部引用: {ref}"
    value: Any = document
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        value = value[part]
    return value


def _walk(value: Any) -> list[Any]:
    values = [value]
    if isinstance(value, dict):
        for item in value.values():
            values.extend(_walk(item))
    elif isinstance(value, list):
        for item in value:
            values.extend(_walk(item))
    return values


def test_langgraph_imports_are_confined_to_runtime_adapters() -> None:
    violations: list[str] = []
    for path in ASSISTANT_SOURCE.rglob("*.py"):
        imports = _langgraph_imports(path)
        if not imports:
            continue
        relative = path.relative_to(ASSISTANT_SOURCE).as_posix()
        allowed = relative.startswith("workflow/") or relative == (
            "adapters/checkpointer_factory.py"
        )
        if not allowed:
            violations.append(f"{relative}: {', '.join(imports)}")

    assert violations == []


def test_agent_domain_does_not_depend_on_outer_layers() -> None:
    forbidden = ("adapters", "api", "services", "tools", "workflow")
    violations: list[str] = []
    for path in (ASSISTANT_SOURCE / "domain").rglob("*.py"):
        for level, module in _imports(path):
            absolute_outer = module.startswith(
                tuple(f"spb_assistant_api.{name}" for name in forbidden)
            )
            relative_outer = level >= 2 and module.startswith(forbidden)
            if absolute_outer or relative_outer:
                violations.append(f"{path.name}: {module}")

    assert violations == []


def test_http_api_does_not_compose_the_fake_agent_gateway() -> None:
    paths = [ASSISTANT_SOURCE / "main.py"]
    paths.extend((ASSISTANT_SOURCE / "api").rglob("*.py"))

    assert not any(
        "fake_tracking" in module
        for path in paths
        for _, module in _imports(path)
    )


def test_v2_openapi_draft_has_valid_internal_refs_and_intents() -> None:
    document = json.loads(OPENAPI_DRAFT.read_text(encoding="utf-8"))

    assert document["openapi"] == "3.1.0"
    assert document["info"]["x-document-status"] == "proposed"
    assert set(document["paths"]) == {
        "/v2/agent/capabilities",
        "/v2/agent/messages",
        "/v2/agent/conversations/{conversation_id}",
    }
    declared_intents = document["components"]["schemas"]["Intent"][
        "enum"
    ]
    assert declared_intents == [intent.value for intent in Intent]

    refs = [
        item["$ref"]
        for item in _walk(document)
        if isinstance(item, dict) and "$ref" in item
    ]
    assert refs
    for ref in refs:
        assert _resolve_local_ref(document, ref) is not None


def test_proposed_v2_contract_is_not_mounted_into_the_v1_service() -> None:
    implemented_paths = set(app.openapi()["paths"])

    assert "/v1/chat" in implemented_paths
    assert not any(path.startswith("/v2/agent") for path in implemented_paths)
