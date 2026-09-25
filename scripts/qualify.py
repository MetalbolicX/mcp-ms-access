"""Fast local qualification gate — mirrors the CI checks exactly.

Runs ruff, pyright, unit tests, and the tool budget in CI order. The full
13-gate qualification matrix remains in scripts/step5_qualify.py and requires
a local Microsoft Access installation.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Gate:
    name: str
    command: tuple[str, ...]


GATES = (
    Gate("ruff", ("ruff", "check", ".")),
    Gate("pyright", ("pyright",)),
    Gate("unit", ("pytest", "tests/unit/", "-q")),
    Gate("budget", ("python", "scripts/tool_budget.py")),
)


def get_gates(skip_pyright: bool = False) -> tuple[Gate, ...]:
    """Return CI gates, optionally omitting the explicitly skippable typecheck."""
    if skip_pyright:
        return tuple(gate for gate in GATES if gate.name != "pyright")
    return GATES


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-pyright",
        action="store_true",
        help="skip the pyright check for quick iteration (not recommended)",
    )
    args = parser.parse_args()

    results: list[tuple[str, str, int | None, float]] = []
    if args.skip_pyright:
        results.append(("pyright", "SKIPPED", None, 0.0))

    for gate in get_gates(skip_pyright=args.skip_pyright):
        started = time.perf_counter()
        try:
            completed = subprocess.run(["uv", "run", *gate.command], check=False)
            exit_code = completed.returncode
        except OSError as error:
            print(f"Could not run {gate.name}: {error}", file=sys.stderr)
            exit_code = 1
        elapsed = time.perf_counter() - started
        status = "OK" if exit_code == 0 else f"FAIL (exit {exit_code})"
        results.append((gate.name, status, exit_code, elapsed))

    print("\nQualification summary:")
    for name, status, _, elapsed in results:
        if status == "SKIPPED":
            print(f"GATE {name:<8} SKIPPED")
        else:
            print(f"GATE {name:<8} {status} ({elapsed:.2f}s)")
    return int(any(exit_code not in (None, 0) for _, _, exit_code, _ in results))


if __name__ == "__main__":
    sys.exit(main())
