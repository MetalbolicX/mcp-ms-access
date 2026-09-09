"""CLI entry point."""

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(prog="ms-access-mcp")
    parser.add_argument("--version", action="version", version="ms-access-mcp 0.1.0")

    subparsers = parser.add_subparsers(dest="command")

    doctor_parser = subparsers.add_parser("doctor", help="Run diagnostics")
    doctor_parser.set_defaults(func=lambda _opts: _doctor_not_implemented())

    opts = parser.parse_args()

    if hasattr(opts, "func"):
        return opts.func(opts)
    return 0


def _doctor_not_implemented() -> int:
    print("doctor: not implemented", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
