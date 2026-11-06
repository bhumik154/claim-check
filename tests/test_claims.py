from pathlib import Path

import pytest

from claim_check.claims import extract_claims


def test_no_claim_returns_empty_list_for_unrelated_message():
    assert extract_claims("Fix the off-by-one error in the loop bounds") == []


def test_extracts_simple_n_passed():
    claims = extract_claims("Ran the suite: 22 passed")
    assert len(claims) == 1
    assert claims[0].kind == "n_passed"
    assert claims[0].claimed_passed == 22
    assert claims[0].claimed_total is None


def test_extracts_n_of_m_slash_form_with_equal_numbers():
    claims = extract_claims("22/22 tests pass now")
    assert len(claims) == 1
    assert claims[0].kind == "n_of_m"
    assert claims[0].claimed_passed == 22
    assert claims[0].claimed_total == 22


def test_extracts_n_of_m_slash_form_with_unequal_numbers():
    # A common real phrasing this must not miss: still catching up, not
    # claiming full success.
    claims = extract_claims("15/17 tests passing so far")
    assert len(claims) == 1
    assert claims[0].kind == "n_of_m"
    assert claims[0].claimed_passed == 15
    assert claims[0].claimed_total == 17


def test_extracts_n_of_m_without_the_word_tests():
    claims = extract_claims("15/17 passing")
    assert len(claims) == 1
    assert claims[0].claimed_passed == 15
    assert claims[0].claimed_total == 17


def test_extracts_all_tests_pass_phrase():
    claims = extract_claims("Fixed the bug, all tests pass")
    assert len(claims) == 1
    assert claims[0].kind == "all_pass"
    assert claims[0].claimed_passed is None
    assert claims[0].claimed_total is None


def test_extracts_all_n_tests_pass_phrase():
    claims = extract_claims("Fixed the bug, all 22 tests pass")
    assert len(claims) == 1
    assert claims[0].kind == "all_pass"
    assert claims[0].claimed_total == 22


def test_ignores_issue_reference_number_adjacent_to_the_word_passed():
    # A contrived but real-shaped false positive: an issue number sitting
    # right next to the word "passed" must not be read as a count.
    claims = extract_claims("closes #22 passed review already")
    assert claims == []


def test_decimal_number_not_split_into_a_false_claim():
    # "0.22" ending in "22" must not be misread as "22 passed".
    claims = extract_claims("coverage improved to 0.22 passed threshold")
    assert claims == []


def test_multiple_claims_of_different_kinds_only_the_last_in_text_is_kept():
    # Confirmed real: compare.py checks every returned claim against one
    # pytest run, so two different-kind claims in the same message can't
    # both be independently true. Only keeping the last one in the text
    # (not one-per-kind) is what makes "14 passed. ... now 15/15 tests
    # pass!" register as a correction instead of a contradiction.
    claims = extract_claims("17 passed. All tests pass.")
    assert len(claims) == 1
    assert claims[0].kind == "all_pass"


def test_case_insensitivity_of_all_tests_pass_phrase():
    claims = extract_claims("ALL TESTS PASS")
    assert len(claims) == 1
    assert claims[0].kind == "all_pass"


def test_negated_all_tests_pass_phrase_is_not_treated_as_a_claim():
    assert extract_claims("not all tests pass yet, still debugging") == []


def test_negated_n_passed_phrase_is_not_treated_as_a_claim():
    # The negation guard was only wired up for all_pass, not n_passed or
    # n_of_m, confirmed directly: "but not 22 passed yet" registered "22
    # passed" as a real claim, which compare.py would then check against
    # the actual run and block an honest, explicitly-negated commit.
    assert extract_claims("Refactored the parser, but not 22 passed yet") == []


def test_negated_n_of_m_phrase_is_not_treated_as_a_claim():
    assert extract_claims("Still not 15/15 tests passing") == []


def test_stale_count_corrected_with_a_different_claim_kind_is_not_a_contradiction():
    # Confirmed real: grouping by kind let an n_passed claim and an n_of_m
    # claim coexist, so a developer restating their own stale count using
    # different phrasing ("14 passed" -> later "15/15 tests pass") was
    # flagged as a mismatch on the stale claim, even though the later
    # statement in the same message is the honest, current one.
    claims = extract_claims("Initial run had 14 passed. Fixed the bug, now 15/15 tests pass!")
    assert len(claims) == 1
    assert claims[0].kind == "n_of_m"
    assert claims[0].claimed_passed == 15
    assert claims[0].claimed_total == 15


def test_partial_ratio_claim_with_unequal_numerator_and_denominator_is_still_extracted():
    claims = extract_claims("Down to 3/22 failing, rest passing: 19/22 tests pass")
    # Two n_of_m-shaped phrases; "3/22 failing" isn't a pass claim at all
    # (no "pass"/"passing" adjacent to it) so only the second is a claim.
    assert len(claims) == 1
    assert claims[0].claimed_passed == 19
    assert claims[0].claimed_total == 22


def test_multiline_commit_message_claim_found_on_a_later_line():
    message = "Fix the scale-invariance bug\n\nVerified directly before fixing.\n\n22 passed"
    claims = extract_claims(message)
    assert len(claims) == 1
    assert claims[0].claimed_passed == 22


def test_last_claim_wins_when_the_same_kind_repeats_with_conflicting_numbers():
    # The exact scenario this project exists for: a number restated and
    # corrected later in the same message. The later one is authoritative.
    message = "Thought it was 15 passed earlier in the session, but it's actually 22 passed"
    claims = extract_claims(message)
    assert len(claims) == 1
    assert claims[0].claimed_passed == 22


def test_ratio_claim_spelled_passed_is_not_degraded_to_a_bare_count_claim():
    # Confirmed bypass: "22/22 passed" produced n_passed(22) instead of
    # n_of_m(22, 22), because _N_PASSED_RE matches the contained substring
    # "22 passed" at a LATER start offset than the enclosing _N_OF_M_RE
    # match, and last-start-wins picked the contained one. The denominator
    # was silently discarded, so a real run of 22 passed + 1 failed
    # verified this claim as true and let the commit through.
    claims = extract_claims("22/22 passed")
    assert len(claims) == 1
    assert claims[0].kind == "n_of_m"
    assert claims[0].claimed_passed == 22
    assert claims[0].claimed_total == 22


def test_ratio_claim_numerator_is_the_pass_count_not_the_denominator():
    # The same overlap read the DENOMINATOR as the pass count: "22/25
    # passed" parsed as n_passed(25). That both false-accepted a lie
    # (real 25 passed -> "match") and false-blocked an honest claim
    # (real 22 passed -> 'claimed "25 passed" but 22 actually passed').
    claims = extract_claims("22/25 passed")
    assert len(claims) == 1
    assert claims[0].kind == "n_of_m"
    assert claims[0].claimed_passed == 22
    assert claims[0].claimed_total == 25


def test_negated_ratio_claim_spelled_passed_is_not_treated_as_a_claim():
    # The negation guard was defeated by the same overlap. The n_of_m match
    # at offset 4 was correctly dropped as negated, but the contained
    # "22 passed" starts at offset 7, where the 20-character lookback window
    # is "not 22/" - no adjacent negation - so it survived and registered a
    # claim. A message asserting the OPPOSITE of a claim then blocked an
    # honest commit. Containment resolution must run BEFORE the negation
    # filter for this to work.
    assert extract_claims("not 22/22 passed") == []


def test_negated_ratio_claim_with_never_is_not_treated_as_a_claim():
    assert extract_claims("never 9/9 passed") == []


def test_thousands_separator_is_not_misread_as_a_smaller_count():
    # "1,022 passed" parsed as 22 (from the substring "022 passed"): the
    # lookbehind blocked ".", "#" and digits, but not ",". An honest claim
    # about a 1022-test suite was flagged as a mismatch.
    claims = extract_claims("1,022 passed")
    assert claims == []


def test_negation_expressed_as_a_contraction_is_honoured():
    # The "n't" branch of the negation regex was unreachable: \b never holds
    # before the "n" of "doesn't"/"didn't"/"isn't", since both neighbours
    # are word characters.
    assert extract_claims("it doesn't 22 passed") == []
    assert extract_claims("that isn't 22 passed") == []


def test_comma_before_a_genuine_claim_still_registers():
    # Guard against over-correcting the thousands-separator fix: a comma
    # separated by whitespace is ordinary prose, not a digit grouping.
    claims = extract_claims("took 3.5s, 22 passed")
    assert len(claims) == 1
    assert claims[0].claimed_passed == 22


def test_a_later_weaker_claim_does_not_silence_an_earlier_stronger_one():
    # Confirmed real: position alone used to decide the winner, so a later
    # but less specific all_pass claim discarded an earlier, more specific
    # (and false) n_of_m claim. "50/50 tests pass" is a lie against a real
    # 3-passed/0-failed run, but "All tests pass now." - true of that same
    # run - came later in the text and silenced it, letting the commit
    # through. Specificity must win regardless of position.
    message = "Fix parser: 50/50 tests pass\n\nAll tests pass now."
    claims = extract_claims(message)
    assert len(claims) == 1
    assert claims[0].kind == "n_of_m"
    assert claims[0].claimed_passed == 50
    assert claims[0].claimed_total == 50


def test_last_claim_wins_when_two_n_of_m_claims_of_equal_specificity_repeat():
    # Equal-specificity tie: falls back to last-position-wins, same
    # principle as the existing n_passed-repeats-twice test, applied to
    # n_of_m instead.
    message = "Thought it was 14/15 tests passing earlier, but it's actually 15/15 tests pass"
    claims = extract_claims(message)
    assert len(claims) == 1
    assert claims[0].kind == "n_of_m"
    assert claims[0].claimed_passed == 15
    assert claims[0].claimed_total == 15


# --- quoted examples are mentions, not claims --------------------------------
#
# Measured across every real end-of-turn message and commit message on the
# author's machine: no genuine claim was ever written in double quotes, and
# about 35 quoted ones were examples being discussed. Eight commits in this
# project's own history would have been blocked for quoting an example.

_FIXTURES = Path(__file__).parent / "fixtures" / "commit_messages"


def test_a_real_commit_quoting_examples_is_judged_on_its_real_count():
    # Commit 544a419: three quoted examples, then the true "370 passed." on
    # its own line. The quoted "all 22 tests pass" used to outrank it on
    # specificity, so the commit would have been blocked against a 370 run.
    message = (_FIXTURES / "quoted_examples_then_real_count.txt").read_text(encoding="utf-8")
    claims = extract_claims(message)
    assert len(claims) == 1
    assert claims[0].kind == "n_passed"
    assert claims[0].claimed_passed == 370


@pytest.mark.parametrize("message", [
    'It checks test-count claims like "22 passed" or "all tests pass" against the runner.',
    "normal users don't write \"22 passed\" in prose",
    'a probe written as "the last full-suite run I observed\nwas 443 passed in 25.68s" was ignored',
    "an honest “all 150 tests pass” got flagged",
    "The detector selects `all 22 tests pass`, then `22/22 passed`.",
    'made `cd docs && git commit -m "6 passed"` run pytest in docs/',
], ids=["examples", "prose", "wrapped", "curly", "code-span", "nested"])
def test_a_quoted_example_is_not_a_claim(message):
    assert extract_claims(message) == []


def test_a_count_written_only_in_a_code_span_is_not_checked():
    # The accepted cost. Genuine counts were written this way three times in
    # the measured corpus, examples about eight, and the text cannot tell the
    # two apart. Missing a check is the safe direction.
    assert extract_claims("Tests: `496 passed`.") == []


def test_a_real_claim_beside_quoted_examples_is_the_one_checked():
    message = (
        'reports `1 passed, 1 deselected`, which makes "all tests pass" true of '
        "what ran.\n\n370 passed."
    )
    claims = extract_claims(message)
    assert [(c.kind, c.claimed_passed) for c in claims] == [("n_passed", 370)]


@pytest.mark.parametrize("message, kind, passed, total", [
    ("Type-check, lint, and all 61 tests pass.", "all_pass", None, 61),
    ("`tsc` clean, `eslint` clean, 153/153 tests passing.", "n_of_m", 153, 153),
    ("It's done and the tests' names are fixed.\n\n370 passed.", "n_passed", 370, None),
], ids=["all-n", "ratio", "apostrophes"])
def test_unquoted_claims_are_unchanged(message, kind, passed, total):
    claims = extract_claims(message)
    assert [(c.kind, c.claimed_passed, c.claimed_total) for c in claims] == [(kind, passed, total)]


def test_quoted_runner_output_in_a_commit_message_is_not_a_claim():
    # Two real commits here carried an indented vitest tally. A later real
    # count masked it; without one, the files line read as "1 passed".
    message = (
        "Support vitest and jest\n\n"
        "vitest prints two tallies:\n\n"
        "    Test Files  1 failed | 1 passed (2)\n"
        "         Tests  1 failed | 3 passed (4)\n"
    )
    assert extract_claims(message) == []


def test_a_pasted_pytest_summary_in_a_commit_message_is_still_a_claim():
    # Only chat strips a bare summary line. In a commit message a pasted
    # summary is how people state the result.
    claims = extract_claims("Fix the parser\n\n22 passed in 1.40s")
    assert [(c.kind, c.claimed_passed) for c in claims] == [("n_passed", 22)]


def test_a_tests_label_in_prose_is_a_claim_not_runner_output():
    # Real vitest and jest tallies end in "(4)" or "6 total". A line that
    # merely starts with "Tests:" is someone stating the result.
    claims = extract_claims("Fix the parser\n\nTests: 22 passed")
    assert [(c.kind, c.claimed_passed) for c in claims] == [("n_passed", 22)]


# --- a stale claim is never checked in place of a skipped one -----------------
#
# Found by the 0.8.0 smoke test. Each of these hid the real, up-to-date claim
# as if it were quoted and fell back to an earlier, stale one, which blocked a
# true commit that 0.7.0 let through.

@pytest.mark.parametrize("message, expected", [
    ('Was 3 passed. Then I fixed "the parser and all 5 tests pass.', "all 5 tests pass"),
    ('Was 3 passed. Then "the fix\r\nand all 5 tests pass.', "all 5 tests pass"),
    ('Was 3 passed. She said "the "x" thing. All 5 tests pass.', "All 5 tests pass"),
    ("Was 3 passed. Press the ` key.\n\nAll 5 tests pass.\n\nSee `foo`.", "All 5 tests pass"),
    ("Was 3 passed.\n\n```\nsome code\n\nAll 5 tests pass.", "All 5 tests pass"),
    ("Results:\n  - before the fix only 3 passed, now\n    all 5 tests pass after the rewrite", "all 5 tests pass"),
    ("Summary:\n- was 3 passed\n    - now all 5 tests pass", "all 5 tests pass"),
    ("Was 3 passed. Er sagte „ok“ und all 5 tests pass.", "all 5 tests pass"),
    ("Was 3 passed. 'cause of the fix all 5 tests pass in the users' suite.", "all 5 tests pass"),
    ("Was 3 passed. Like the '90s, all 5 tests pass, it's fine'.", "all 5 tests pass"),
], ids=["unclosed", "unclosed-crlf", "unbalanced-outer", "lone-backtick", "open-fence",
        "continuation", "nested-bullet", "german", "cause", "nineties"])
def test_the_up_to_date_claim_is_checked_not_a_stale_one(message, expected):
    assert [c.raw_text for c in extract_claims(message)] == [expected]


@pytest.mark.parametrize("message", [
    'Was 3 passed. Now **"all 5 tests pass"** for real.',
    "Was 3 passed. ``a` and all 5 tests pass `b``",
    'Tests: 22 passed. The tool flags claims like "all 5 tests pass".',
], ids=["emphasis", "odd-backticks", "real-then-example"])
def test_skipping_a_quoted_claim_never_falls_back_to_an_earlier_one(message):
    # When the claim that would have been checked is quoted, only a claim
    # written after it may take its place. An earlier one is as likely to be
    # the stale count the quoted text superseded, so nothing is checked. The
    # last case is the price: a real count before an example goes unchecked.
    assert extract_claims(message) == []


def test_a_count_stated_before_pasted_output_is_still_checked():
    # Pasted output is evidence, not a newer claim by the writer, so the rule
    # against falling back to an earlier claim does not apply to it.
    message = "Fix the parser\n\n22 passed.\n\n    Test Files  1 failed | 1 passed (2)\n"
    assert [c.raw_text for c in extract_claims(message)] == ["22 passed"]
