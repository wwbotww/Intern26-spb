from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

from spb_assistant_api.api.agent_contracts import AgentApiDependencies
from spb_assistant_api.api.app import create_app
from spb_assistant_api.domain.intents import Intent
from spb_assistant_api.main import app
from spb_assistant_api.settings import AssistantSettings


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


def test_application_services_do_not_depend_on_runtime_or_adapters() -> None:
    forbidden = ("adapters", "api", "workflow")
    violations: list[str] = []
    for path in (ASSISTANT_SOURCE / "services").rglob("*.py"):
        for level, module in _imports(path):
            absolute_outer = module.startswith(
                tuple(
                    f"spb_assistant_api.{name}"
                    for name in forbidden
                )
            )
            relative_outer = level >= 2 and module.startswith(forbidden)
            if absolute_outer or relative_outer:
                violations.append(f"{path.name}: {module}")

    assert violations == []


def test_http_api_does_not_compose_fake_agent_gateways() -> None:
    paths = [ASSISTANT_SOURCE / "main.py"]
    paths.extend((ASSISTANT_SOURCE / "api").rglob("*.py"))

    assert not any(
        module.startswith("spb_assistant_api.adapters.fake_")
        or (level >= 2 and module.startswith("adapters.fake_"))
        for path in paths
        for level, module in _imports(path)
    )


def test_v2_http_adapter_does_not_import_workflow_or_adapters() -> None:
    paths = [
        ASSISTANT_SOURCE / "api" / "agent_contracts.py",
        ASSISTANT_SOURCE / "api" / "agent_schemas.py",
        ASSISTANT_SOURCE / "api" / "routes" / "agent.py",
    ]
    forbidden = ("adapters", "workflow")
    violations: list[str] = []
    for path in paths:
        for level, module in _imports(path):
            absolute_outer = module.startswith(
                tuple(
                    f"spb_assistant_api.{name}"
                    for name in forbidden
                )
            )
            relative_outer = level >= 2 and module.startswith(forbidden)
            if absolute_outer or relative_outer:
                violations.append(f"{path.name}: {module}")

    assert violations == []


def test_agent_tools_do_not_depend_on_adapters_or_workflow() -> None:
    forbidden = ("adapters", "api", "workflow")
    violations: list[str] = []
    for path in (ASSISTANT_SOURCE / "tools").rglob("*.py"):
        for level, module in _imports(path):
            absolute_outer = module.startswith(
                tuple(
                    f"spb_assistant_api.{name}"
                    for name in forbidden
                )
            )
            relative_outer = level >= 2 and module.startswith(forbidden)
            if absolute_outer or relative_outer:
                violations.append(f"{path.name}: {module}")

    assert violations == []


def test_adapters_do_not_depend_on_api_tools_or_workflow() -> None:
    forbidden = ("api", "tools", "workflow")
    violations: list[str] = []
    for path in (ASSISTANT_SOURCE / "adapters").rglob("*.py"):
        for level, module in _imports(path):
            absolute_outer = module.startswith(
                tuple(
                    f"spb_assistant_api.{name}"
                    for name in forbidden
                )
            )
            relative_outer = level >= 2 and module.startswith(forbidden)
            if absolute_outer or relative_outer:
                violations.append(f"{path.name}: {module}")

    assert violations == []


def test_v2_openapi_phase4d_has_valid_internal_refs_and_intents() -> None:
    document = json.loads(OPENAPI_DRAFT.read_text(encoding="utf-8"))

    assert document["openapi"] == "3.1.0"
    assert document["info"]["x-document-status"] == (
        "partially-implemented"
    )
    assert set(document["paths"]) == {
        "/v2/agent/capabilities",
        "/v2/agent/health/ready",
        "/v2/agent/messages",
        "/v2/agent/conversations/{conversation_id}",
    }
    readiness = document["paths"]["/v2/agent/health/ready"]["get"]
    assert readiness["security"] == []
    assert set(readiness["responses"]) == {"200", "503"}
    declared_intents = document["components"]["schemas"]["Intent"][
        "enum"
    ]
    assert declared_intents == [intent.value for intent in Intent]
    assert document["components"]["schemas"]["PublicIntent"]["enum"] == [
        intent.value for intent in Intent if intent is not Intent.UNKNOWN
    ]
    message_content = document["paths"]["/v2/agent/messages"]["post"][
        "responses"
    ]["200"]["content"]
    assert set(message_content) == {
        "application/json",
        "text/event-stream",
    }
    stream_schema = message_content["text/event-stream"]["schema"]
    assert len(stream_schema["oneOf"]) == 7
    assert (
        document["components"]["schemas"]["AgentStreamDoneEvent"]
        ["properties"]["response"]["$ref"]
        == "#/components/schemas/AgentResponse"
    )

    refs = [
        item["$ref"]
        for item in _walk(document)
        if isinstance(item, dict) and "$ref" in item
    ]
    assert refs
    for ref in refs:
        assert _resolve_local_ref(document, ref) is not None


def test_v2_contract_remains_opt_in_for_the_default_v1_service() -> None:
    implemented_paths = set(app.openapi()["paths"])

    assert "/v1/chat" in implemented_paths
    assert not any(path.startswith("/v2/agent") for path in implemented_paths)

    opted_in = create_app(
        settings=AssistantSettings(
            auth_enabled=False,
            rate_limit_enabled=False,
            metrics_enabled=False,
        ),
        agent_api=AgentApiDependencies(
            service=object(),  # type: ignore[arg-type]
            capabilities={},
        ),
    )
    opted_in_paths = set(opted_in.openapi()["paths"])
    assert {
        "/v2/agent/capabilities",
        "/v2/agent/health/ready",
        "/v2/agent/messages",
        "/v2/agent/conversations/{conversation_id}",
    } <= opted_in_paths
