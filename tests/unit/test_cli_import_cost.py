"""Importing the CLI must not pull the FastMCP server chain.

The `mcp` subcommand is the only consumer of ms_access_mcp.server; every
other CLI verb (including --version) must stay import-light.
"""

import subprocess
import sys


def test_cli_import_does_not_load_mcp_server() -> None:
    code = "import sys; import ms_access_mcp.cli; assert 'ms_access_mcp.server' not in sys.modules, 'server imported eagerly'"
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
