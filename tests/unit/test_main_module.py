"""Pin `python -m ms_access_mcp` dispatch (portable-runtime entry)."""

from __future__ import annotations

import subprocess
import sys


def test_module_main_version_exits_zero() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ms_access_mcp", "--version"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
