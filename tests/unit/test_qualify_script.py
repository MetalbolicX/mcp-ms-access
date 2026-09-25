import sys
from pathlib import Path


def qualify_module():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import qualify

    return qualify


def test_gates_match_ci_order() -> None:
    qualify = qualify_module()
    gates = qualify.GATES
    assert [(gate.name, gate.command) for gate in gates] == [
        ("ruff", ("ruff", "check", ".")),
        ("pyright", ("pyright",)),
        ("unit", ("pytest", "tests/unit/", "-q")),
        ("budget", ("python", "scripts/tool_budget.py")),
    ]


def test_skip_pyright_removes_only_that_gate() -> None:
    qualify = qualify_module()
    skipped = qualify.get_gates(skip_pyright=True)
    assert [gate.name for gate in skipped] == ["ruff", "unit", "budget"]
    assert len(qualify.GATES) - len(skipped) == 1
