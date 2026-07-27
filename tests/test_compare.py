from claim_check.compare import compare_claims
from claim_check.models import Claim, PytestCounts


def _counts(passed=0, failed=0, skipped=0, xfailed=0, xpassed=0, errors=0, warnings=0, deselected=0):
    return PytestCounts(
        passed=passed,
        failed=failed,
        skipped=skipped,
        xfailed=xfailed,
        xpassed=xpassed,
        errors=errors,
        warnings=warnings,
        deselected=deselected,
        duration_s=0.1,
        raw_summary_line="fixture",
    )


def _n_passed(n):
    return Claim(kind="n_passed", claimed_passed=n, claimed_total=None, raw_text=f"{n} passed", span=(0, 0))


def _n_of_m(n, m):
    return Claim(kind="n_of_m", claimed_passed=n, claimed_total=m, raw_text=f"{n}/{m} passing", span=(0, 0))


def _all_pass(total=None):
    text = f"all {total} tests pass" if total is not None else "all tests pass"
    return Claim(kind="all_pass", claimed_passed=None, claimed_total=total, raw_text=text, span=(0, 0))


def test_no_claim_never_flags_regardless_of_actual_failures():
    verdict = compare_claims([], _counts(passed=0, failed=99))
    assert verdict.status == "no_claim"


def test_matching_n_passed_claim_returns_match():
    verdict = compare_claims([_n_passed(22)], _counts(passed=22))
    assert verdict.status == "match"


def test_mismatched_n_passed_claim_returns_mismatch_naming_both_numbers():
    verdict = compare_claims([_n_passed(22)], _counts(passed=21))
    assert verdict.status == "mismatch"
    assert "22 passed" in verdict.message
    assert "21" in verdict.message


def test_n_of_m_claim_matches_exactly():
    verdict = compare_claims([_n_of_m(20, 22)], _counts(passed=20, failed=2))
    assert verdict.status == "match"


def test_n_of_m_claim_mismatch_on_failures():
    verdict = compare_claims([_n_of_m(22, 22)], _counts(passed=20, failed=2))
    assert verdict.status == "mismatch"


def test_all_tests_pass_claim_matches_on_zero_failures_regardless_of_exact_count():
    verdict = compare_claims([_all_pass()], _counts(passed=17, failed=0))
    assert verdict.status == "match"


def test_all_tests_pass_claim_mismatches_on_any_failure():
    verdict = compare_claims([_all_pass()], _counts(passed=16, failed=1))
    assert verdict.status == "mismatch"


def test_all_n_tests_pass_claim_flags_mismatch_on_wrong_total_even_with_zero_failures():
    # Zero failures alone isn't enough if a specific total was claimed and
    # the actual total collected doesn't match it.
    verdict = compare_claims([_all_pass(total=22)], _counts(passed=17, failed=0))
    assert verdict.status == "mismatch"


def test_all_tests_pass_claim_with_zero_collected_tests_is_flagged_not_silently_matched():
    # Vacuously true if you only check failed == 0; almost certainly not
    # what was meant by "all tests pass".
    verdict = compare_claims([_all_pass()], _counts(passed=0, failed=0))
    assert verdict.status == "mismatch"


def test_multiple_claims_one_wrong_yields_overall_mismatch_naming_the_wrong_one():
    verdict = compare_claims([_n_passed(22), _all_pass()], _counts(passed=22, failed=1))
    assert verdict.status == "mismatch"
    assert "all tests pass" in verdict.message


def test_multiple_claims_all_correct_yields_match():
    verdict = compare_claims([_n_passed(22), _all_pass()], _counts(passed=22, failed=0))
    assert verdict.status == "match"


def test_n_passed_claim_with_an_error_present_is_flagged_even_though_the_count_is_literally_correct():
    # Confirmed against real pytest: a broken fixture on one test doesn't
    # stop the other 22 from running, producing an entirely ordinary-
    # looking "22 passed, 1 error" summary. "22 passed" is literally true,
    # but the true denominator is unknown - an error means some test never
    # got a chance to pass or fail. Before the errors>0 guard was added,
    # this compared 22 == 22 and returned a silent match.
    verdict = compare_claims([_n_passed(22)], _counts(passed=22, errors=1))
    assert verdict.status == "mismatch"
    assert "error" in verdict.message


def test_all_tests_pass_claim_with_zero_failures_but_an_error_present_is_flagged():
    verdict = compare_claims([_all_pass()], _counts(passed=17, failed=0, errors=1))
    assert verdict.status == "mismatch"


def test_n_of_m_claim_with_an_error_present_is_flagged_even_when_n_and_m_both_match():
    verdict = compare_claims([_n_of_m(20, 20)], _counts(passed=20, failed=0, errors=1))
    assert verdict.status == "mismatch"


def test_zero_errors_does_not_trigger_the_error_guard():
    # The guard must key specifically on errors > 0, not merely exist -
    # ordinary passing runs (errors=0, the default) must be unaffected.
    verdict = compare_claims([_n_passed(22)], _counts(passed=22, errors=0))
    assert verdict.status == "match"


def test_runner_error_yields_runner_error_status_not_mismatch():
    verdict = compare_claims([_n_passed(22)], None)
    assert verdict.status == "runner_error"


def test_runner_error_with_no_claim_present_still_yields_no_claim_not_runner_error():
    # Ordering matters: "no claim at all" takes priority over "counts are
    # missing", since there being no claim means nothing needed checking in
    # the first place, independent of whether a run even happened.
    verdict = compare_claims([], None)
    assert verdict.status == "no_claim"
