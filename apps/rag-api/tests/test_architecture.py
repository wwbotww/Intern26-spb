from __future__ import annotations

import ast
from pathlib import Path


def _assert_forbidden_imports(
    source_root: Path,
    forbidden_prefixes: tuple[str, ...],
) -> None:
    violations: list[str] = []
    for path in source_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            if any(
                name.startswith(prefix)
                for name in names
                for prefix in forbidden_prefixes
            ):
                violations.append(f"{path}:{node.lineno}")

    assert violations == []


def test_online_package_does_not_import_offline_pipeline() -> None:
    project_root = Path(__file__).parents[3]
    _assert_forbidden_imports(
        project_root / "apps" / "rag-api" / "src" / "spb_rag_api",
        ("spb_pipeline",),
    )


def test_offline_package_does_not_import_online_api() -> None:
    project_root = Path(__file__).parents[3]
    _assert_forbidden_imports(
        project_root / "apps" / "offline-pipeline" / "src" / "spb_pipeline",
        ("spb_rag_api",),
    )


def test_contracts_package_does_not_import_either_application() -> None:
    project_root = Path(__file__).parents[3]
    _assert_forbidden_imports(
        project_root / "packages" / "contracts" / "src" / "spb_contracts",
        ("spb_pipeline", "spb_rag_api"),
    )


def test_eval_package_uses_api_as_a_black_box() -> None:
    project_root = Path(__file__).parents[3]
    _assert_forbidden_imports(
        project_root / "eval" / "src" / "spb_eval",
        ("spb_pipeline", "spb_rag_api", "pymilvus", "spb_contracts"),
    )
