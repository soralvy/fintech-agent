"""The canonical full verification gate.

    uv run python scripts/verify.py

Runs, in order, and stops at the first failure:

1. ``uv lock --check``
2. ``uv run ruff format --check .``
3. ``uv run ruff check .``
4. ``uv run mypy`` (paths come from ``[tool.mypy] files``)
5. ``uv run pytest``, the full suite, with a temporary JUnit XML report

Full verification includes the PostgreSQL integration tests, so
``TEST_DATABASE_URL`` must name the disposable ``*_test`` database. This script
never infers, creates, or prints a database URL; the test fixtures' guard
(``tests/db_safety.py``) decides whether a reset is safe. A run in which any
test was skipped fails, because a skipped database test proves nothing.

Exit status: the failing step's own status; 2 when ``TEST_DATABASE_URL`` is
unset; 1 when tests were skipped or no usable report was produced.

Standard library only, so it runs before anything else can be trusted.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

TEST_DATABASE_URL_ENV = "TEST_DATABASE_URL"
REPO_ROOT = Path(__file__).resolve().parent.parent

STATIC_STEPS: tuple[tuple[str, ...], ...] = (
    ("uv", "lock", "--check"),
    ("uv", "run", "ruff", "format", "--check", "."),
    ("uv", "run", "ruff", "check", "."),
    ("uv", "run", "mypy"),
)
PYTEST_STEP: tuple[str, ...] = ("uv", "run", "pytest")

EXIT_MISSING_DATABASE = 2
EXIT_INCOMPLETE_TESTS = 1


@dataclass(frozen=True, slots=True)
class JunitTotals:
    """Counts from a pytest JUnit XML report."""

    tests: int
    failures: int
    errors: int
    skipped: list[str]


def read_junit(path: Path) -> JunitTotals:
    """Summarize a pytest JUnit XML report.

    Skips are read from each ``<testcase>``'s ``<skipped>`` element rather
    than from human-readable output, so the count cannot drift with pytest's
    formatting.
    """
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    skipped = [
        f"{case.get('classname', '')}::{case.get('name', '')}"
        for suite in suites
        for case in suite.iter("testcase")
        if case.find("skipped") is not None
    ]
    return JunitTotals(
        tests=sum(int(suite.get("tests", "0")) for suite in suites),
        failures=sum(int(suite.get("failures", "0")) for suite in suites),
        errors=sum(int(suite.get("errors", "0")) for suite in suites),
        skipped=skipped,
    )


def run(command: Sequence[str]) -> int:
    """Run one step from the repository root, streaming its output."""
    print(f"==> {' '.join(command)}", flush=True)
    return subprocess.run(command, cwd=REPO_ROOT, check=False).returncode


def _failed(command: Sequence[str], status: int) -> int:
    print(f"verify: FAILED: {' '.join(command)} (exit {status})", file=sys.stderr)
    return status


def _run_pytest() -> JunitTotals | int:
    """Run the full suite; return its totals, or the exit status to stop with.

    The report lives in a temporary directory that is removed on every path.
    """
    with tempfile.TemporaryDirectory(prefix="verify-") as scratch:
        report = Path(scratch) / "pytest-junit.xml"
        status = run((*PYTEST_STEP, f"--junitxml={report}"))
        if status != 0:
            return _failed(PYTEST_STEP, status)
        if not report.is_file():
            print("verify: FAILED: pytest wrote no JUnit report", file=sys.stderr)
            return EXIT_INCOMPLETE_TESTS
        return read_junit(report)


def _check_totals(totals: JunitTotals) -> int:
    """Fail a green pytest run that did not actually run every test."""
    if totals.tests == 0:
        print("verify: FAILED: pytest collected no tests", file=sys.stderr)
        return EXIT_INCOMPLETE_TESTS
    if totals.skipped:
        print(
            f"verify: FAILED: {len(totals.skipped)} test(s) skipped; "
            "full verification requires every test to run:",
            file=sys.stderr,
        )
        for test_id in totals.skipped:
            print(f"  {test_id}", file=sys.stderr)
        return EXIT_INCOMPLETE_TESTS
    print(f"verify: PASSED: all 5 steps; {totals.tests} tests, 0 skipped")
    return 0


def main() -> int:
    if not os.environ.get(TEST_DATABASE_URL_ENV, "").strip():
        print(
            f"verify: {TEST_DATABASE_URL_ENV} is not set. Full verification runs "
            "the database tests against the disposable *_test database; this "
            "script never creates or infers one.",
            file=sys.stderr,
        )
        return EXIT_MISSING_DATABASE

    for step in STATIC_STEPS:
        status = run(step)
        if status != 0:
            return _failed(step, status)

    outcome = _run_pytest()
    if isinstance(outcome, int):
        return outcome
    return _check_totals(outcome)


if __name__ == "__main__":
    sys.exit(main())
