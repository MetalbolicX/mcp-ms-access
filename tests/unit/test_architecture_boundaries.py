"""CI guard: ensure no web/CLI framework imports leak into the deterministic core.

This test imports ms_access_mcp modules in an isolated subprocess and asserts
that none of the forbidden frameworks appear in sys.modules after import.

It also scans the whole ``src/ms_access_mcp`` tree with stdlib ``ast`` and
asserts the repo's layer-direction contract in executable form:

- ``backends/`` must not import from ``application``, ``mcp_tools``, ``cli``,
  or ``server`` (dependency rule: cli/mcp_tools -> application -> backends).
- The shared kernel (``errors.py``, ``models.py``, ``safety.py``,
  ``profiles.py``, ``capabilities.py``) must import nothing from any layer.
- No module may import a ``_private`` name across package boundaries.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

FORBIDDEN = [
    "fastapi",
    "starlette",
    "jinja2",
    "typer",
    "uvicorn",
    "fastmcp",
]

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "ms_access_mcp"

# Layers above backends that backends must never import (relative to the
# ms_access_mcp package root).
HIGHER_LAYERS = ("application", "mcp_tools", "cli", "server")

# Shared kernel modules live directly under ms_access_mcp/ and must import
# nothing from any layer.
KERNEL_MODULES = ("errors", "models", "safety", "profiles", "capabilities")


@pytest.mark.parametrize("module", ["ms_access_mcp", "ms_access_mcp.safety", "ms_access_mcp.models", "ms_access_mcp.errors"])
def test_no_forbidden_frameworks_in_core(module: str):
    """After importing the core package, no web/CLI frameworks may be loaded."""
    code = (
        f"import sys; import importlib; importlib.import_module('{module}'); "
        + "; ".join(f'assert "{pkg}" not in sys.modules, f"{{pkg}} should not be loaded in core"'
        for pkg in FORBIDDEN)
        + "; print('OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"Import or framework leak detected for {module}: {result.stderr}"


# =============================================================================
# AST layer-direction and private-import guard (plan 014, post-012 state)
# =============================================================================


def _module_parts(rel_path: Path) -> tuple[tuple[str, ...], bool]:
    """Full dotted module parts and whether the file is a package ``__init__``.

    ``backends/dao.py`` -> ``(("ms_access_mcp", "backends", "dao"), False)``;
    ``backends/__init__.py`` -> ``(("ms_access_mcp", "backends"), True)``.
    """
    parts = rel_path.with_suffix("").parts
    is_package = parts[-1] == "__init__"
    if is_package:
        parts = parts[:-1]
    return ("ms_access_mcp",) + parts, is_package


def _resolve_from_target(
    current: tuple[str, ...], is_package: bool, node: ast.ImportFrom
) -> tuple[str, ...]:
    """Resolve the module an ImportFrom refers to.

    Level 1 means "the current package": for a regular module that is its
    parent, for a package ``__init__`` it is the package itself. Level N
    then walks up N-1 more packages.
    """
    if node.level == 0:
        anchor: tuple[str, ...] = ()
    else:
        base = current if is_package else current[:-1]
        anchor = base[: len(base) - node.level + 1]
    if node.module:
        return anchor + tuple(node.module.split("."))
    return anchor


def _top_layer(parts: tuple[str, ...]) -> str | None:
    """Return the layer segment for targets inside ms_access_mcp, else None."""
    if len(parts) >= 2 and parts[0] == "ms_access_mcp":
        return parts[1]
    return None


def _find_boundary_violations() -> list[str]:
    """Scan every module under src/ms_access_mcp and collect violations."""
    violations: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        rel = path.relative_to(SRC_ROOT)
        if "__pycache__" in rel.parts:
            continue
        current, is_package = _module_parts(rel)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        imported_targets: list[tuple[str, ...]] = []
        private_imports: list[tuple[tuple[str, ...], str]] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                target = _resolve_from_target(current, is_package, node)
                imported_targets.append(target)
                for alias in node.names:
                    # Dunder names (e.g. __future__) are never private API.
                    if alias.name.startswith("_") and not alias.name.startswith("__"):
                        private_imports.append((target, alias.name))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported_targets.append(tuple(alias.name.split(".")))

        in_backends = current[1] == "backends" if len(current) > 1 else False
        in_kernel = current[1:] in {(m,) for m in KERNEL_MODULES}

        for target in imported_targets:
            layer = _top_layer(target)
            if layer is None:
                continue
            if in_backends and layer in HIGHER_LAYERS:
                violations.append(
                    f"{rel}: backends module imports from '{layer}' ({target})"
                )
            if in_kernel and layer in HIGHER_LAYERS + ("backends",):
                violations.append(
                    f"{rel}: kernel module imports from '{layer}' ({target})"
                )

        # Cross-package private-name imports: the defining package of a
        # `_private` name is the package containing the source module; the
        # importer must live in that same package.
        current_pkg = current[:-1]
        for target, name in private_imports:
            defining_pkg = target[:-1]
            if defining_pkg != current_pkg:
                violations.append(
                    f"{rel}: imports private name '{name}' from package "
                    f"{defining_pkg or '(root)'} (defining package != {current_pkg or '(root)'})"
                )

    return violations


def test_layer_direction_and_private_imports():
    """The AST guard encodes the repo's architectural contract (post-012).

    Fails on any forbidden edge; the failure message lists every violation.
    """
    violations = _find_boundary_violations()
    assert violations == [], (
        "Forbidden import edges detected:\n" + "\n".join(f"  - {v}" for v in violations)
    )
