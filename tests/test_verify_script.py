"""The canonical gate runner's decisions, with its steps replaced.

No real command runs here: ``run`` is swapped for a recorder, so these tests
cover ordering, exit-status propagation, the skip rule, and cleanup.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from scripts import verify

PASSING_REPORT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="2" failures="0" errors="0" skipped="0">
<testcase classname="tests.test_a" name="test_one"/>
<testcase classname="tests.test_a" name="test_two"/>
</testsuite></testsuites>
"""

SKIPPING_REPORT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="2" failures="0" errors="0" skipped="1">
<testcase classname="tests.test_a" name="test_one"/>
<testcase classname="tests.test_db" name="test_needs_db">
<skipped type="pytest.skip" message="TEST_DATABASE_URL is not set"/>
</testcase>
</testsuite></testsuites>
"""


def test_read_junit_counts_skips_from_testcase_elements(tmp_path: Path) -> None:
    report = tmp_path / "report.xml"
    report.write_text(SKIPPING_REPORT)

    totals = verify.read_junit(report)

    assert (totals.tests, totals.failures, totals.errors) == (2, 0, 0)
    assert totals.skipped == ["tests.test_db::test_needs_db"]


Recorder = Callable[[Sequence[str]], int]


def recorder(
    calls: list[tuple[str, ...]],
    *,
    fail_on: str | None = None,
    status: int = 3,
    report: str | None = PASSING_REPORT,
    reports: list[Path] | None = None,
) -> Recorder:
    """A ``run`` stand-in that records commands and writes a pytest report."""

    def fake_run(command: Sequence[str]) -> int:
        calls.append(tuple(command))
        if fail_on is not None and fail_on in command:
            return status
        for argument in command:
            if argument.startswith("--junitxml="):
                path = Path(argument.removeprefix("--junitxml="))
                if reports is not None:
                    reports.append(path)
                if report is not None:
                    path.write_text(report)
        return 0

    return fake_run


@pytest.fixture(autouse=True)
def _test_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://localhost/x_test")


def test_full_mode_runs_every_step_in_order_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[str, ...]] = []
    reports: list[Path] = []
    monkeypatch.setattr(verify, "run", recorder(calls, reports=reports))

    assert verify.main() == 0

    assert [call[:4] for call in calls] == [
        ("uv", "lock", "--check"),
        ("uv", "run", "ruff", "format"),
        ("uv", "run", "ruff", "check"),
        ("uv", "run", "mypy"),
        ("uv", "run", "pytest", f"--junitxml={reports[0]}"),
    ]
    assert not reports[0].exists(), "the temporary report must be removed"
    assert "2 tests, 0 skipped" in capsys.readouterr().out


def test_missing_test_database_fails_before_any_step(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(verify, "run", recorder(calls))
    monkeypatch.setenv("TEST_DATABASE_URL", "  ")

    assert verify.main() == verify.EXIT_MISSING_DATABASE
    assert calls == []
    assert "TEST_DATABASE_URL is not set" in capsys.readouterr().err


@pytest.mark.parametrize("failing", ["lock", "format", "check", "mypy", "pytest"])
def test_the_first_failing_status_is_propagated_and_later_steps_do_not_run(
    monkeypatch: pytest.MonkeyPatch, failing: str
) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(verify, "run", recorder(calls, fail_on=failing, status=7))

    assert verify.main() == 7
    assert failing in calls[-1]


def test_skipped_tests_fail_verification(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(verify, "run", recorder([], report=SKIPPING_REPORT))

    assert verify.main() == verify.EXIT_INCOMPLETE_TESTS
    assert "tests.test_db::test_needs_db" in capsys.readouterr().err


def test_a_missing_report_fails_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verify, "run", recorder([], report=None))

    assert verify.main() == verify.EXIT_INCOMPLETE_TESTS
