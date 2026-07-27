from pathlib import Path

from claim_check.pytest_parser import parse_summary_line, strip_ansi

FIXTURES = Path(__file__).parent / "fixtures" / "pytest_outputs"


def _read(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parses_all_passed_simple():
    counts = parse_summary_line(_read("all_passed.txt"))
    assert counts is not None
    assert counts.passed == 22
    assert counts.total == 22
    assert counts.failed == 0


def test_parses_failed_and_passed_mixed():
    counts = parse_summary_line(_read("one_failed_rest_passed.txt"))
    assert counts.failed == 1
    assert counts.passed == 21
    assert counts.total == 22


def test_parses_passed_with_warning_suffix_excludes_warning_from_total():
    # "36 passed, 1 warning in 43.25s" - the warning count must not leak
    # into .total, a warning is not a test result.
    counts = parse_summary_line(_read("passed_with_warning.txt"))
    assert counts.passed == 36
    assert counts.warnings == 1
    assert counts.total == 36


def test_parses_skipped_and_xfailed_combo():
    counts = parse_summary_line(_read("mixed_skip_xfail.txt"))
    assert counts.failed == 3
    assert counts.passed == 5
    assert counts.skipped == 2
    assert counts.xfailed == 1
    assert counts.xpassed == 1
    assert counts.total == 3 + 5 + 2 + 1 + 1


def test_parses_no_tests_ran_as_zero_total_not_a_parse_error():
    # "no tests ran" must return a real PytestCounts (all zero), not None -
    # a claim of "all tests pass" against this should be a mismatch
    # (nothing ran), which requires counts to exist to compare against.
    counts = parse_summary_line(_read("no_tests_ran.txt"))
    assert counts is not None
    assert counts.total == 0
    assert counts.duration_s == 0.01


def test_parses_summary_with_ansi_color_codes_stripped_first():
    counts = parse_summary_line(_read("ansi_colored.txt"))
    assert counts is not None
    assert counts.passed == 14
    assert counts.total == 14


def test_strip_ansi_removes_escape_sequences_directly():
    colored = "\x1b[32mhello\x1b[0m"
    assert strip_ansi(colored) == "hello"


def test_uses_last_summary_line_when_multiple_present():
    # xdist workers print their own per-worker summary lines before the
    # real aggregate one; only the final line is authoritative.
    counts = parse_summary_line(_read("multiple_summary_lines.txt"))
    assert counts.failed == 1
    assert counts.passed == 7
    assert counts.total == 8


def test_errors_only_line_excluded_from_total():
    # A collection error means 0 tests actually ran; errors must not
    # silently count as passed or failed tests.
    counts = parse_summary_line(_read("collection_error.txt"))
    assert counts is not None
    assert counts.errors == 1
    assert counts.total == 0


def test_deselected_count_tracked_but_excluded_from_total():
    counts = parse_summary_line("========== 18 passed, 4 deselected in 0.10s ==========")
    assert counts.passed == 18
    assert counts.deselected == 4
    assert counts.total == 18


def test_returns_none_when_pytest_crashes_before_any_summary():
    # An INTERNALERROR has no summary line and no "no tests ran" line at
    # all - this must be structurally distinguishable from "0 tests ran".
    counts = parse_summary_line(_read("internal_error_crash.txt"))
    assert counts is None


def test_returns_none_for_completely_unrelated_text():
    assert parse_summary_line("this is not pytest output at all") is None
