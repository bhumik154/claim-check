"""Finding the parts of a text that are quoted rather than asserted.

Measured across every real end-of-turn message and commit message on the
author's machine, not one genuine test-count claim was written inside double
quotes, while about 35 quoted ones were examples being discussed. These tests
pin how quotation is recognised; tests/test_claims.py pins what that does to
claim selection.
"""

import random
import time

import pytest

from claim_check.quoting import quoted_regions


def quoted_texts(text):
    return [text[a:b] for a, b in quoted_regions(text)]


@pytest.mark.parametrize("text, expected", [
    ('claims like "22 passed" here', ['"22 passed"']),
    ("tests show `22 passed` here", ["`22 passed`"]),
    ("curly “all tests pass” here", ["“all tests pass”"]),
    ("curly ‘all tests pass’ here", ["‘all tests pass’"]),
    ("German „all tests pass“ here", ["„all tests pass“"]),
    ('two "a" and "b"', ['"a"', '"b"']),
], ids=["double", "code-span", "curly-double", "curly-single", "german", "two"])
def test_each_kind_of_quotation_is_found(text, expected):
    assert quoted_texts(text) == expected


def test_plain_text_has_no_quoted_regions():
    assert quoted_regions("All 61 tests pass. 370 passed.") == []


def test_empty_text_has_no_quoted_regions():
    assert quoted_regions("") == []


def test_a_double_quote_spans_a_wrapped_line():
    # Commit messages wrap at 72 columns, and two real mentions in this
    # project's history were split across a line break.
    text = 'a probe written as "the last full-suite run I observed\nwas 443 passed in 25.68s" was ignored'
    assert quoted_texts(text) == ['"the last full-suite run I observed\nwas 443 passed in 25.68s"']


@pytest.mark.parametrize("text", [
    'Was 3 passed. Then I fixed "the parser and all 5 tests pass.',
    'He wrote "all tests pass and\nthen stopped.\n\n370 passed.',
    'He wrote "all tests pass\r\n\r\n370 passed.',
    'Was 3 passed. “the parser and all 5 tests pass.',
], ids=["same-line", "paragraph", "crlf", "curly"])
def test_an_unclosed_quote_quotes_nothing(text):
    # Guessing where an unclosed quotation ends is not safe in either
    # direction: treating the rest of the paragraph as quoted hid the real
    # "all 5 tests pass" and left the stale "3 passed" to be checked, which
    # blocked a true commit. Found by the 0.8.0 smoke test, 1,996 times in a
    # 40,000-message fuzz.
    assert quoted_regions(text) == []


def test_a_closed_quote_inside_an_unclosed_one_is_still_quoted():
    text = 'She said "the "all 5 tests pass" claim is wrong.\n\n4 passed.'
    assert quoted_texts(text) == ['"all 5 tests pass"']


def test_quote_marks_inside_a_code_span_are_not_quotation_marks():
    text = 'run `git commit -m "6 passed"` then 370 passed'
    assert quoted_texts(text) == ['`git commit -m "6 passed"`']


def test_a_double_backtick_span_can_hold_a_single_backtick():
    text = "see ``a`b`` and 370 passed"
    assert quoted_texts(text) == ["``a`b``"]


def test_an_unclosed_backtick_is_a_literal_character():
    # Markdown's rule: a backtick run with no matching run is just text.
    assert quoted_regions("a stray ` then 370 passed") == []


def test_a_code_span_may_span_a_line_break():
    text = "the line `22\npassed` here"
    assert quoted_texts(text) == ["`22\npassed`"]


def test_a_code_span_never_crosses_a_blank_line():
    # Markdown's rule too. A lone backtick ("press the ` key") paired with
    # one several paragraphs later and hid every claim in between.
    text = "Was 3 passed. Press the ` key.\n\nAll 5 tests pass.\n\nSee `foo`."
    assert quoted_texts(text) == ["`foo`"]


@pytest.mark.parametrize("text", [
    "it's done, 370 passed",
    "the tests' names don't matter, 370 passed",
    "rock 'n' roll aside, 370 passed",
    "'cause of the fix 370 passed in the users' suite",
    "Like the '90s, 370 passed, it's fine'.",
    "single 'all tests pass' here",
], ids=["contraction", "possessive", "rock-n-roll", "cause", "nineties", "single-pair"])
def test_straight_single_quotes_are_never_quotation(text):
    # An apostrophe and a single quote are the same character, and "'cause"
    # or "'90s" opened a quote that hid a real claim. No real example in the
    # measured corpus was written in single quotes outside double ones.
    assert quoted_regions(text) == []


def test_a_curly_single_quote_pair_must_stay_on_one_line():
    text = "he said ‘all\ntests pass’ then"
    assert quoted_regions(text) == []


def test_single_quotes_inside_double_quotes_are_covered_by_the_outer_quote():
    text = "If I write \"this tool catches claims like 'all tests pass'\", I'm giving an example."
    regions = quoted_regions(text)
    start = text.index("all tests pass")
    assert any(a <= start < b for a, b in regions)


def test_regions_are_sorted_and_do_not_overlap():
    text = 'one "a `b` c" two `d "e" f` three “g”'
    regions = quoted_regions(text)
    assert regions == sorted(regions)
    for (a1, b1), (a2, b2) in zip(regions, regions[1:]):
        assert b1 <= a2


def test_non_string_input_has_no_regions():
    assert quoted_regions(None) == []


def test_adversarial_input_is_parsed_in_linear_time():
    # The heredoc stash was once quadratic on a crafted command. This runs in
    # a hook with a time budget, so a megabyte of quote characters has to be
    # cheap.
    rng = random.Random(7)
    text = "".join(rng.choice("\"'`“”‘’ a\n") for _ in range(1_000_000))
    start = time.perf_counter()
    quoted_regions(text)
    assert time.perf_counter() - start < 2.0


def test_a_quotation_inside_a_quotation_stays_quoted():
    # Pairing straight quotes in order leaves the inner example outside
    # every quote. Seen in a real end-of-turn message.
    text = 'For example, "people don\'t write "22 passed" in prose" gets checked.'
    start = text.index("22 passed")
    assert any(a <= start < b for a, b in quoted_regions(text))


def test_an_inch_mark_does_not_quote_the_rest_of_the_paragraph():
    text = 'Fits a 5" screen. 370 passed.'
    assert quoted_regions(text) == []


def test_an_inch_mark_does_not_break_a_later_quotation():
    text = 'Fits a 5" screen, and "22 passed" is an example.'
    assert quoted_texts(text) == ['"22 passed"']


@pytest.mark.parametrize("text", [
    "\n" * 100_000 + "4 passed",
    " \n" * 100_000,
    ">\n" * 100_000,
    "    x\n" * 50_000,
    "1 passed. " * 10_000,
], ids=["newlines", "space-lines", "quote-lines", "indented-lines", "many-claims"])
def test_pathological_messages_are_handled_quickly(text):
    # The blockquote pattern matched whitespace across line breaks: 100,000
    # blank lines took 9 seconds on the commit path and 3 minutes in 0.7.0's
    # end-of-turn check, whose budget is 10 seconds. Dropping enclosed claims
    # compared every pair, so 10,000 claims took 20 seconds.
    from claim_check import conversation
    from claim_check.claims import extract_claims

    start = time.perf_counter()
    extract_claims(text)
    conversation.extract_claims(text)
    assert time.perf_counter() - start < 2.0


@pytest.mark.parametrize("text", [
    "1 passed " * 20_000,
    "Tests " + "1 passed " * 20_000,
], ids=["counts", "tests-label"])
def test_one_long_line_of_counts_is_handled_quickly(text):
    # The summary-line and tally patterns each had two ".*" on one line, so a
    # single 180 KB line took 16 seconds in the end-of-turn check.
    from claim_check import conversation
    from claim_check.claims import extract_claims

    start = time.perf_counter()
    extract_claims(text)
    conversation.extract_claims(text)
    assert time.perf_counter() - start < 2.0


def test_a_quote_right_after_an_opening_bracket_opens():
    # Found dogfooding 0.9.0's own commit message: ("`make check` passes")
    # was not seen as quoted, because a quote after "(" also counted as
    # able to close.
    text = 'one ("`make check` and `make webcheck` pass"), and more'
    assert quoted_texts(text) == ['"`make check` and `make webcheck` pass"']
    assert quoted_texts('a ("22 passed") b') == ['"22 passed"']
