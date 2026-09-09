"""CLI entry point."""

import argparse
import sys

from .doctor import doctor_main


def main() -> int:
    parser = argparse.ArgumentParser(prog="ms-access-mcp")
    parser.add_argument("--version", action="version", version="ms-access-mcp 0.1.0")

    subparsers = parser.add_subparsers(dest="command")

    doctor_parser = subparsers.add_parser("doctor", help="Run diagnostics")
    doctor_parser.add_argument("--json", action="store_true", help="JSON output")
    doctor_parser.add_argument("--deep", action="store_true", help="Deep checks including Access create+release")
    doctor_parser.set_defaults(
        func=lambda opts: doctor_main(deep=opts.deep, json_output=opts.json)
    )

    opts = parser.parse_args()

    if hasattr(opts, "func"):
        return opts.func(opts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
