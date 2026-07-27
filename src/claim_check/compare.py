"""The policy core: given claims extracted from a commit message and the
real test-runner counts, decides whether they match. Every
false-positive-avoidance decision in this project lives in this one file,
reviewed as a single unit rather than scattered across three entry points.
"""

from typing import Optional

from .models import Claim, RunResult, PytestCounts, Verdict


def evaluate(message: str, run_result: RunResult) -> Verdict:
    from .claims import extract_claims

    claims = extract_claims(message)
    return compare_claims(claims, run_result.counts)


def compare_claims(claims: list[Claim], counts: Optional[PytestCounts]) -> Verdict:
    if not claims:
        # No test-count claim was made at all. Never flags, regardless of
        # the actual test outcome - this is the one non-negotiable rule.
        return Verdict(
            status="no_claim",
            message="No test-count claim found in the message; nothing to verify.",
            claims=[],
            counts=counts,
        )

    if counts is None:
        # The test runner crashed before producing a normal result. This is
        # not evidence any claim is wrong, just that it couldn't be
        # checked - resolved to fail open, not block the commit.
        return Verdict(
            status="runner_error",
            message="Could not verify: the test runner did not produce a parseable result.",
            claims=claims,
            counts=None,
        )

    problems = [detail for claim in claims if (detail := _mismatch_reason(claim, counts)) is not None]

    if problems:
        return Verdict(status="mismatch", message="; ".join(problems), claims=claims, counts=counts)

    return Verdict(status="match", message="All test-count claims match the actual results.", claims=claims, counts=counts)


def _mismatch_reason(claim: Claim, counts: PytestCounts) -> Optional[str]:
    """Returns None if the claim matches, otherwise a human-readable reason
    it doesn't."""
    if counts.errors > 0:
        # A collection failure (a broken import) or a fixture setup/teardown
        # error doesn't stop the rest of the suite from running - confirmed
        # directly, a single broken fixture on one test still lets 22 other
        # tests run and pass normally, producing a completely ordinary-
        # looking "22 passed, 1 error" summary. "22 passed" is literally
        # true, but the true denominator is unknown: an error means some
        # test never got a chance to pass or fail, so no count-based claim
        # can be verified as complete. Checked before any claim-specific
        # comparison, so it applies uniformly to every claim kind.
        plural = "" if counts.errors == 1 else "s"
        return f'claimed "{claim.raw_text}" but the run reported {counts.errors} error{plural}; the total is unverified'

    if claim.kind == "n_passed":
        if claim.claimed_passed != counts.passed:
            return f'claimed "{claim.raw_text}" but {counts.passed} actually passed'
        return None

    if claim.kind == "n_of_m":
        if claim.claimed_passed != counts.passed or claim.claimed_total != counts.total:
            return f'claimed "{claim.raw_text}" but actual result is {counts.passed}/{counts.total}'
        return None

    if claim.kind == "all_pass":
        if counts.total == 0:
            # Vacuously true if you only check failed == 0; almost
            # certainly not what was meant by "all tests pass".
            return f'claimed "{claim.raw_text}" but 0 tests were collected'
        if counts.failed != 0:
            return f'claimed "{claim.raw_text}" but {counts.failed} test(s) failed'
        if claim.claimed_total is not None and claim.claimed_total != counts.total:
            return f'claimed "{claim.raw_text}" but actual total is {counts.total}'
        return None

    raise ValueError(f"unknown claim kind: {claim.kind!r}")
