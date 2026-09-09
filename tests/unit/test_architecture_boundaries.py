"""CI guard: ensure no web/CLI framework imports leak into the deterministic core.

This test imports ms_access_mcp modules in an isolated subprocess and asserts
that none of the forbidden frameworks appear in sys.modules after import.
"""

import subprocess
import sys

import pytest

FORBIDDEN = [
    "fastapi",
    "starlette",
    "jinja2",
    "typer",
    "uvicorn",
    "fastmcp",
]


@pytest.mark.parametrize("module", ["ms_access_mcp", "ms_access_mcp.safety", "ms_access_mcp.models", "ms_access_mcp.errors"])
def test_no_forbidden_frameworks_in_core(module: str):
    """After importing the core package, no web/CLI frameworks may be loaded."""
    code = f"import sys; import importlib; importlib.import_module('{module}'); sys.exit(0)"
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"Import failed for {module}: {result.stderr}"

    # Check sys.modules for forbidden packages
    check_code = (
        "import sys; "
        + "; ".join(f'assert "{pkg}" not in sys.modules, f"{{pkg}} should not be loaded in core"'
        for pkg in FORBIDDEN)
        + "; print('OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", check_code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"Framework leak detected: {result.stderr}"
