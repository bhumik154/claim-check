"""Failure lines in real tool output, captured from actual runs.

See tests/fixtures/tool_outputs/README.md for where each file came from. A
failure line is what lets a run be blamed on one command: the exit code of a
Bash call is usually `tail`'s, not the tool's.
"""

from pathlib import Path

import pytest

from claim_check.tool_failures import failure_lines

OUTPUTS = Path(__file__).parent / "fixtures" / "tool_outputs"
PYTEST = Path(__file__).parent / "fixtures" / "pytest_outputs"


def families(name, command="", folder=OUTPUTS):
    text = (folder / name).read_text(encoding="utf-8")
    return sorted({family for family, _line in failure_lines(text, command)})


@pytest.mark.parametrize("name, expected", [
    ("go-test-fail.txt", ["go test"]),
    ("go-test-fail-v.txt", ["go test"]),
    ("make-check-fail.txt", ["go test", "make"]),
    ("tsc-fail.txt", ["tsc"]),
    ("eslint-fail.txt", ["eslint"]),
    ("npm-run-lint-fail.txt", ["eslint"]),
    ("npm-run-typecheck-fail.txt", ["tsc"]),
    ("pnpm-run-lint-fail.txt", ["eslint", "pnpm"]),
], ids=lambda value: value if isinstance(value, str) else None)
def test_each_real_failure_is_found(name, expected):
    assert families(name) == expected


@pytest.mark.parametrize("name", [
    "go-test-pass.txt", "make-check-pass.txt", "tsc-pass.txt", "eslint-pass.txt",
    "npm-run-lintok-pass.txt",
    # go vet prints only file:line messages; only its exit code tells.
    "go-vet-fail.txt",
])
def test_no_failure_line_in_a_pass(name):
    assert families(name) == []


def test_a_test_runner_summary_with_failures_is_a_failure_line():
    assert families("one_failed_rest_passed.txt", "python -m pytest -q", PYTEST) == ["tests"]
    assert families("all_passed.txt", "python -m pytest -q", PYTEST) == []


def test_eslint_warnings_alone_are_not_a_failure():
    assert failure_lines("✖ 3 problems (0 errors, 3 warnings)\n", "npx eslint .") == []


def test_the_failure_line_is_returned_as_printed():
    [(family, line)] = failure_lines((OUTPUTS / "tsc-fail.txt").read_text(encoding="utf-8"), "npx tsc --noEmit")
    assert family == "tsc"
    assert line.startswith("src/bad.ts(1,7): error TS2322")


def test_ansi_colour_does_not_hide_a_failure_line():
    assert failure_lines("\x1b[31mmake: *** [Makefile:4: test] Error 1\x1b[0m\n", "make check")


def test_a_megabyte_of_output_is_scanned_quickly():
    import time
    text = ("--- PASS: TestX (0.00s)\n" * 40_000) + "FAIL\texample.com/x\t0.1s\n"
    start = time.perf_counter()
    assert failure_lines(text, "go test ./...")
    assert time.perf_counter() - start < 2.0


def test_npms_echo_of_a_script_command_is_not_a_failure_line():
    # npm prints "> <script body>" before running it. Found in the live check:
    # a script whose command text held "error TS" matched as if tsc had failed.
    output = "\n> app@1.0.0 check\n> node scripts/report.js --pattern 'src/a.ts(1,1): error TS1005: x'\n\nclean\n"
    assert failure_lines(output, "npm run check") == []
