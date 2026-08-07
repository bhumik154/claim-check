"""Dispatching to the right parser, and knowing which runs are scoped.

Two jobs that have to stay in step. If the parser understands a runner but
the scope guard does not, a filtered run gets recorded as whole-suite
evidence and can confirm a claim a full run would have contradicted. That is
worse than not supporting the runner at all, so the guard treats an
unrecognised runner as scoped.
"""

import shlex

import pytest

from claim_check.runners import parse_test_output
from claim_check.evidence import is_scoped

PYTEST_OUTPUT = (
    "============================= test session starts ====================\n"
    "========================= 22 passed in 0.30s =========================\n"
)
PYTEST_QUIET = "..\n2 passed in 0.01s\n"
VITEST_OUTPUT = " Test Files  1 passed (1)\n      Tests  3 passed (3)\n   Duration  577ms\n"
JEST_OUTPUT = (
    "Test Suites: 1 passed, 1 total\n"
    "Tests:       1 skipped, 1 todo, 3 passed, 5 total\n"
    "Time:        0.485 s\n"
)


def test_pytest_output_is_parsed():
    assert parse_test_output(PYTEST_OUTPUT).passed == 22


def test_quiet_pytest_output_is_parsed():
    assert parse_test_output(PYTEST_QUIET).passed == 2


def test_vitest_output_is_parsed():
    counts = parse_test_output(VITEST_OUTPUT)
    assert counts.passed == 3
    assert counts.total == 3


def test_jest_output_is_parsed():
    counts = parse_test_output(JEST_OUTPUT)
    assert counts.passed == 3
    assert counts.total == 5


def test_each_parser_declines_the_others_output():
    # Dispatch order must not decide correctness. If both parsers accepted
    # the same text, whichever ran first would silently win.
    from claim_check.js_parser import parse_js_summary
    from claim_check.pytest_parser import parse_summary_line

    assert parse_js_summary(PYTEST_OUTPUT) is None
    assert parse_js_summary(PYTEST_QUIET) is None
    assert parse_summary_line(VITEST_OUTPUT) is None
    assert parse_summary_line(JEST_OUTPUT) is None


def test_ordinary_command_output_is_not_a_test_run():
    assert parse_test_output("total 0\ndrwxr-xr-x 2 x x 4096 .\n") is None
    assert parse_test_output("Compiled successfully in 1.2s\n") is None


def test_non_string_input_yields_nothing():
    assert parse_test_output(None) is None
    assert parse_test_output(42) is None


# --- scope, per runner -------------------------------------------------------

@pytest.mark.parametrize(
    "command",
    [
        "npx vitest run",
        "cd frontend && npx vitest run",
        "npm exec vitest run 2>&1 | tail -5",
        "npx jest",
        "cd app && npx jest --ci",
        "yarn jest",
    ],
)
def test_whole_suite_js_runs_are_not_scoped(command):
    assert is_scoped(shlex.split(command)) is False


@pytest.mark.parametrize(
    "command",
    [
        "npx vitest run -t login",                  # name filter
        "npx vitest run --testNamePattern=login",
        "npx vitest run src/auth.test.ts",          # a path narrows collection
        "npx vitest run --changed",
        "npx vitest run --bail 1",
        "npx vitest run --shard=1/3",
        "npx jest -t login",
        "npx jest --testPathPattern=auth",
        "npx jest --onlyChanged",
        "npx jest --findRelatedTests src/a.js",
        "npx jest --bail",
        "npx jest src/auth.test.js",
    ],
)
def test_scope_narrowing_js_runs_are_marked_scoped(command):
    assert is_scoped(shlex.split(command)) is True


@pytest.mark.parametrize(
    "command",
    [
        "npm test",          # the runner is hidden behind a script
        "npm run test",
        "yarn test",
        "make test",
        "./scripts/ci.sh",
    ],
)
def test_a_hidden_runner_is_treated_as_scoped(command):
    # These may well run everything, but nothing here can establish that, and
    # unknown scope must never be usable as whole-suite evidence.
    assert is_scoped(shlex.split(command)) is True


def test_pytest_scope_rules_are_unchanged():
    # The JS support must not disturb the runner that already worked.
    assert is_scoped(shlex.split("cd /repo && python -m pytest -q")) is False
    assert is_scoped(shlex.split("pytest -k slow")) is True
    assert is_scoped(shlex.split("pytest tests/unit")) is True
