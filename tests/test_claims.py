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
