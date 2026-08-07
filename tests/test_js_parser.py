"""Parsing vitest and jest tallies.

Every fixture here is verbatim output from a real run: vitest 2.1.9 and
jest 29.7.0, both on Windows with Node 24.18.0. Nothing is paraphrased,
because the three defects this project found in production all came from
building on an assumed format.

The trap both runners share: they print a file-level tally BEFORE the
test-level one. vitest calls it "Test Files", jest calls it "Test Suites".
Taking the first match reports a file count as a test count.
"""

from claim_check.js_parser import parse_js_summary

VITEST_PASS = (
    " Test Files  1 passed (1)\n"
    "      Tests  3 passed (3)\n"
    "   Start at  02:46:16\n"
    "   Duration  577ms (transform 18ms, setup 0ms, collect 19ms)\n"
)

VITEST_FAIL = (
    " Test Files  1 failed | 1 passed (2)\n"
    "      Tests  1 failed | 3 passed (4)\n"
    "   Duration  559ms\n"
)

JEST_PASS = (
    "Test Suites: 1 passed, 1 total\n"
    "Tests:       1 skipped, 1 todo, 3 passed, 5 total\n"
    "Snapshots:   0 total\n"
    "Time:        0.485 s\n"
    "Ran all test suites.\n"
)

JEST_FAIL = (
    "Test Suites: 1 failed, 1 passed, 2 total\n"
    "Tests:       1 failed, 1 skipped, 1 todo, 3 passed, 6 total\n"
    "Snapshots:   0 total\n"
    "Time:        0.503 s, estimated 1 s\n"
    "Ran all test suites.\n"
)


def test_vitest_all_passing():
    counts = parse_js_summary(VITEST_PASS)
    assert counts is not None
    assert counts.passed == 3
    assert counts.failed == 0
    assert counts.total == 3


def test_vitest_with_a_failure():
    counts = parse_js_summary(VITEST_FAIL)
    assert counts.passed == 3
    assert counts.failed == 1
    assert counts.total == 4


def test_jest_all_passing_counts_todo_and_skipped_toward_the_total():
    # jest states the total outright: "1 skipped, 1 todo, 3 passed, 5 total".
    # A todo test does not run, same as a skipped one, so both land in
    # `skipped` and the computed total matches what jest itself reports.
    counts = parse_js_summary(JEST_PASS)
    assert counts.passed == 3
    assert counts.failed == 0
    assert counts.skipped == 2
    assert counts.total == 5


def test_jest_with_a_failure():
    counts = parse_js_summary(JEST_FAIL)
    assert counts.passed == 3
    assert counts.failed == 1
    assert counts.skipped == 2
    assert counts.total == 6


def test_the_file_level_tally_is_never_read_as_the_test_tally():
    # The whole trap. vitest's "Test Files 1 passed (1)" and jest's
    # "Test Suites: 1 passed, 1 total" both come FIRST and both count files.
    assert parse_js_summary(VITEST_PASS).passed == 3   # not 1
    assert parse_js_summary(JEST_PASS).passed == 3     # not 1
    assert parse_js_summary(VITEST_FAIL).total == 4    # not 2
    assert parse_js_summary(JEST_FAIL).total == 6      # not 2


def test_a_file_tally_with_no_test_tally_yields_nothing():
    # Half a result is not a result. Better to report nothing than to fall
    # back on the file counts.
    assert parse_js_summary("Test Suites: 1 passed, 1 total\n") is None
    assert parse_js_summary(" Test Files  1 passed (1)\n") is None


def test_a_stated_total_that_contradicts_the_parts_is_rejected():
    # The safety property that makes an unknown label safe: jest and vitest
    # both state their total, so a label this parser does not recognise makes
    # the arithmetic disagree, and disagreement returns None rather than a
    # quietly undercounted result.
    assert parse_js_summary("Tests:       3 passed, 9 total\n") is None


def test_ansi_colour_is_stripped_before_parsing():
    coloured = (
        " \x1b[2mTest Files\x1b[22m  \x1b[1m\x1b[32m1 passed\x1b[39m\x1b[22m\x1b[90m (1)\x1b[39m\n"
        "      \x1b[2mTests\x1b[22m  \x1b[1m\x1b[32m3 passed\x1b[39m\x1b[22m\x1b[90m (3)\x1b[39m\n"
    )
    assert parse_js_summary(coloured).passed == 3


def test_no_tests_at_all_yields_nothing():
    # Neither runner prints a tally when nothing matched. jest emits pattern
    # diagnostics, vitest a single sentence. Both mean "could not verify".
    assert parse_js_summary("No test files found, exiting with code 1\n") is None
    assert parse_js_summary("  2 files checked.\n  testMatch: ... - 0 matches\n") is None


def test_pytest_output_is_not_claimed_by_this_parser():
    # Each parser must decline what is not its own, or dispatch order would
    # silently decide correctness.
    pytest_output = (
        "============================= test session starts ====================\n"
        "========================= 22 passed in 0.30s =========================\n"
    )
    assert parse_js_summary(pytest_output) is None


def test_the_raw_tally_line_is_preserved_for_reporting():
    counts = parse_js_summary(JEST_FAIL)
    assert "3 passed" in counts.raw_summary_line
    assert "Test Suites" not in counts.raw_summary_line


def test_duration_is_read_when_present_and_zero_otherwise():
    assert parse_js_summary(JEST_PASS).duration_s == 0.485
    assert parse_js_summary(VITEST_PASS).duration_s == 0.577
    assert parse_js_summary("Tests:       3 passed, 3 total\n").duration_s == 0.0


def test_malformed_and_non_string_input_yields_nothing():
    assert parse_js_summary(None) is None
    assert parse_js_summary(42) is None
    assert parse_js_summary("") is None
    assert parse_js_summary("Tests:       banana passed, 3 total\n") is None
