"""Module entry point: ``python -m ms_access_mcp`` (portable runtime)."""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
