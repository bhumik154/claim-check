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


def test_no_tests_ran_line_is_not_double_counted_by_the_generic_summary_regex():
    # The generic summary regex ("... in Xs ...") also matches a
    # "no tests ran in 0.01s" line at the exact same span, since it doesn't
    # know about that phrasing specifically. Confirmed directly: without
    # filtering out that overlap, aggregation counted the same line twice
    # and doubled the reported duration (0.02 instead of the real 0.01).
    counts = parse_summary_line(_read("no_tests_ran.txt"))
    assert counts.duration_s == 0.01


def test_parses_summary_with_ansi_color_codes_stripped_first():
    counts = parse_summary_line(_read("ansi_colored.txt"))
    assert counts is not None
    assert counts.passed == 14
    assert counts.total == 14


def test_strip_ansi_removes_escape_sequences_directly():
    colored = "\x1b[32mhello\x1b[0m"
    assert strip_ansi(colored) == "hello"


def test_real_pytest_xdist_output_parses_to_a_single_correct_result():
    # Captured from an actual pytest -n 2 run, not fabricated: real xdist
    # prints exactly one final aggregate summary line and no per-worker
    # partial summary lines at all, confirmed directly before writing this
    # (an earlier version of this test used a fabricated fixture with
    # invented per-worker summary lines that real xdist never produces).
    counts = parse_summary_line(_read("real_xdist_output.txt"))
    assert counts.failed == 1
    assert counts.passed == 4
    assert counts.total == 5


def test_multiple_summary_lines_from_piped_suites_are_aggregated_not_last_wins():
    # The real scenario multiple summary lines actually come from: separate
    # pytest invocations piped together, e.g.
    # "pytest tests/unit && pytest tests/integration". A claim like
    # "all 100 tests pass" refers to the combined total (90 + 10), not
    # whichever suite happened to run last (which would wrongly compare
    # against just 10).
    counts = parse_summary_line(_read("multi_suite_piped.txt"))
    assert counts.passed == 100
    assert counts.total == 100


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


def test_a_summary_line_printed_by_a_test_cannot_forge_the_count():
    # Confirmed spoof, reproduced end to end: a failing test that prints
    # "==== 1 passed in 0.01s ====" gets that line echoed back by pytest's
    # own failure report (captured stdout is replayed there), and the old
    # parser aggregated it with the real summary - turning a true 21-passed
    # result into 22 and verifying a false "22 passed" claim as correct.
    # A test's output is always replayed BEFORE its session's real summary
    # line, so taking the last summary line per session defeats this.
    counts = parse_summary_line(_read("forged_summary_in_failure_report.txt"))
    assert counts is not None
    assert counts.passed == 21
    assert counts.failed == 1
    assert counts.total == 22


def test_a_version_shaped_duration_does_not_crash_the_parser():
    # The duration pattern was "[\\d.]+", which accepts "1.2.3" and then
    # raised ValueError inside float(). Reachable in ordinary use: pytest
    # replays a failing test's captured stdout, so a test printing a deploy
    # or build log line was enough to abort the commit with a traceback.
    output = (
        "============================= test session starts =============================\n"
        "==================== deploy finished in 1.2.3s ====================\n"
        "========================= 1 passed in 0.10s ========================\n"
    )
    counts = parse_summary_line(output)
    assert counts is not None
    assert counts.passed == 1
    assert counts.duration_s == 0.10


def test_a_version_shaped_duration_alone_is_not_a_summary_line():
    assert parse_summary_line("==== deploy finished in 1.2.3s ====") is None


def test_a_very_long_separator_line_parses_in_linear_time():
    # The old regex ("^=+\\s*(?P<body>.*?)\\s+in\\s+...") backtracked
    # quadratically when "=+" and ".*?" competed for the same characters:
    # 40k "=" took ~5s, and a test printing "=" * 60000 made the commit-msg
    # hook take 12 seconds. --timeout guards the subprocess, not parsing.
    import time

    payload = "=" * 200000
    started = time.perf_counter()
    assert parse_summary_line(payload) is None
    assert time.perf_counter() - started < 1.0


def test_a_long_near_miss_body_reaches_the_regex_and_still_parses_in_linear_time():
    # The test above proves nothing about _SUMMARY_BODY_RE: "=" * 200000
    # strips down to an empty `core` in _classify_line, so the line returns
    # None from the "if not core" fast path before the regex ever runs. That
    # only demonstrates the strip is fast, not that the regex is linear.
    #
    # This payload starts with "=" but leaves a large, non-empty `core`
    # after .strip("=").strip(), so _SUMMARY_BODY_RE genuinely runs against
    # it. Many repeated " in 1 " substrings are the adversarial shape for
    # "^(?P<body>.*?)\\s+in\\s+\\d+(?:\\.\\d+)?s$": ".*?" must try matching
    # up through each "in 1" occurrence and fail the trailing "s$" check
    # (there's no "s" after each "1"), backtracking further each time under
    # a quadratic implementation.
    import time

    payload = "= " + ("in 1 " * 40000)
    core = payload.strip().strip("=").strip()
    assert core  # confirms this payload actually reaches _SUMMARY_BODY_RE

    started = time.perf_counter()
    assert parse_summary_line(payload) is None
    assert time.perf_counter() - started < 1.0


def test_summary_lines_within_one_session_take_the_last_not_the_sum():
    output = (
        "============================= test session starts =============================\n"
        "==== 5 passed in 1.00s ====\n"
        "==== 7 passed in 2.00s ====\n"
    )
    counts = parse_summary_line(output)
    assert counts.passed == 7
    assert counts.duration_s == 2.00


def test_output_with_no_session_header_is_treated_as_a_single_segment():
    # Guards the real_xdist_output.txt fixture, captured mid-stream with no
    # header, and any trimmed CI log fed through --pytest-output.
    counts = parse_summary_line("========== 18 passed, 4 deselected in 0.10s ==========")
    assert counts.passed == 18
    assert counts.deselected == 4


def test_non_string_input_returns_none_instead_of_raising():
    assert parse_summary_line(None) is None
    assert parse_summary_line(12345) is None


def test_forged_header_before_forged_summary_is_safe_real_count_wins():
    # Ordering matters for the segmentation defense: a forged session header
    # printed BEFORE a forged summary line closes out the segment that the
    # forged line lives in, so the forged line is stranded mid-segment (not
    # last) and the real trailing summary line wins. Confirmed directly:
    # this ordering returns the real 21 passed, not the forged 999.
    output = (
        "============================= test session starts =============================\n"
        "=================================== FAILURES ==================================\n"
        "----------------------------- Captured stdout call ----------------------------\n"
        "============================= test session starts =============================\n"
        "==== 999 passed in 1.0s ====\n"
        "========================= 1 failed, 21 passed in 0.09s ========================\n"
    )
    counts = parse_summary_line(output)
    assert counts is not None
    assert counts.passed == 21
    assert counts.failed == 1
    assert counts.total == 22


def test_forged_summary_before_forged_header_is_a_documented_limitation_not_a_regression():
    # KNOWN, ACCEPTED, DOCUMENTED limitation of the segmentation design -
    # this is characterizing existing behavior, NOT asserting it is
    # desirable. Do NOT "fix" this by changing pytest_parser.py to make
    # this test pass differently; if you harden the parser against this
    # case, UPDATE this test's expected values and the limitation note in
    # the README together, since that's the whole point of pinning it here.
    #
    # A Task 2 reviewer hand-traced this ordering and concluded it was safe.
    # That conclusion was wrong, confirmed directly: when a forged summary
    # line is printed BEFORE a forged session header (rather than after),
    # the forged header closes the segment right after the forged line,
    # making the forged line that segment's LAST line - so it is believed
    # instead of the real summary that follows in the next segment. The
    # threat model this parser defends against is accidental miscounting
    # and casual copy-paste forgery; anyone who can make a test print
    # arbitrary text to fake this exact ordering can already edit the test
    # suite directly, so this gap is accepted rather than closed.
    output = (
        "============================= test session starts =============================\n"
        "=================================== FAILURES ==================================\n"
        "----------------------------- Captured stdout call ----------------------------\n"
        "==== 999 passed in 1.0s ====\n"
        "============================= test session starts =============================\n"
        "========================= 1 failed, 21 passed in 0.09s ========================\n"
    )
    counts = parse_summary_line(output)
    assert counts is not None
    assert counts.passed == 1020
    assert counts.failed == 1
    assert counts.total == 1021
