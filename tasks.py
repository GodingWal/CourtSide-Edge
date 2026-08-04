"""Repo task runner. No Make, no Docker, no build system to learn.

uv run python tasks.py check     # everything CI would run
uv run python tasks.py lint
uv run python tasks.py types
uv run python tasks.py test
uv run python tasks.py gates     # leakage + data-quality gates only
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence

# What mypy is pointed at. Narrower than the lint surface on purpose: `research/` is exploratory
# and the video sandbox lives outside the workspace.
ROOT_PACKAGES = ["packages", "services/wnba_services", "apps", "tests", "tasks.py"]

# What ruff is pointed at -- the whole tree, matching CI exactly. These used to differ, so
# `tasks.py check` could pass against a repository CI would reject, which is the worst possible
# arrangement: the local gate reports success and the remote one finds the problem.
LINT_TARGETS = ["."]


def _run(name: str, cmd: Sequence[str]) -> bool:
    print(f"\n\033[1m>> {name}\033[0m: {' '.join(cmd)}")
    completed = subprocess.run(cmd, check=False)
    ok = completed.returncode == 0
    print(f"   {'PASS' if ok else 'FAIL'} ({name})")
    return ok


def lint() -> bool:
    return _run("ruff format", ["ruff", "format", "--check", *LINT_TARGETS]) & _run(
        "ruff lint", ["ruff", "check", *LINT_TARGETS]
    )


def types() -> bool:
    return _run("mypy strict", ["mypy", *ROOT_PACKAGES])


def test() -> bool:
    return _run("pytest", ["pytest", "-m", "not network"])


def gates() -> bool:
    """The two suites that exist to stop us fooling ourselves."""
    return _run("leakage gate", ["pytest", "tests/leakage", "-v"]) & _run(
        "data-quality gate", ["pytest", "tests/data_quality", "-v"]
    )


def check() -> bool:
    # Bitwise & rather than `and` so every stage runs and the full picture is reported.
    return lint() & types() & test()


TASKS = {"lint": lint, "types": types, "test": test, "gates": gates, "check": check}


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "check"
    task = TASKS.get(name)
    if task is None:
        print(f"unknown task {name!r}; choose from {', '.join(TASKS)}")
        return 2
    ok = task()
    print(f"\n\033[1m{name}: {'PASS' if ok else 'FAIL'}\033[0m")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
