"""Regex-based extraction of test-count claims from commit-message text.

Deliberately not NLP: a small, fully enumerable set of phrasings, each with
an explicit guard against the false-positive shapes that actually show up
in real commit messages (decimal numbers, issue references, negation).
"""

import re

from .models import Claim

# (?<![.\d#,]) blocks four false-positive shapes in one lookbehind: a digit
# preceded by another digit (a partial match into a larger number), by "."
# (the tail of a decimal like "0.22"), by "#" (an issue reference like
# "#22"), or by "," (the tail of a grouped number like "1,022", which
# otherwise parsed as a claim of 22). None of these are test-count claims.
_NOT_A_CLAIM_DIGIT_PREFIX = r"(?<![.\d#,])"

_N_PASSED_RE = re.compile(
    _NOT_A_CLAIM_DIGIT_PREFIX + r"\b(\d+)\s+passed\b",
    re.IGNORECASE,
)
_N_OF_M_RE = re.compile(
    _NOT_A_CLAIM_DIGIT_PREFIX + r"\b(\d+)\s*/\s*(\d+)\s+(?:tests?\s+)?pass(?:ed|ing)?\b",
    re.IGNORECASE,
)
_ALL_PASS_RE = re.compile(
    r"\ball\s+(?:(\d+)\s+)?tests?\s+pass(?:ed|ing)?\b",
    re.IGNORECASE,
)
# "n't" deliberately carries no \b prefix: in a real contraction ("doesn't",
# "isn't") both neighbours of the "n" are word characters, so \b never holds
# there and the branch was dead code.
_NEGATION_BEFORE_RE = re.compile(r"(?:\bnot|\bnever|n't)\s*$", re.IGNORECASE)

# When two matches cover the exact same span, the kind carrying more
# information wins. Not currently reachable given the three regexes above;
# guarded so a future regex change degrades predictably rather than
# arbitrarily by list order.
_KIND_SPECIFICITY = {"n_of_m": 2, "all_pass": 1, "n_passed": 0}


def _is_negated(message: str, match_start: int) -> bool:
    window = message[max(0, match_start - 20) : match_start]
    return bool(_NEGATION_BEFORE_RE.search(window))


def _collect_candidates(message: str) -> list[Claim]:
    candidates: list[Claim] = []

    for m in _N_PASSED_RE.finditer(message):
        candidates.append(
            Claim(
                kind="n_passed",
                claimed_passed=int(m.group(1)),
                claimed_total=None,
                raw_text=m.group(0),
                span=m.span(),
            )
        )

    for m in _N_OF_M_RE.finditer(message):
        candidates.append(
            Claim(
                kind="n_of_m",
                claimed_passed=int(m.group(1)),
                claimed_total=int(m.group(2)),
                raw_text=m.group(0),
                span=m.span(),
            )
        )

    for m in _ALL_PASS_RE.finditer(message):
        candidates.append(
            Claim(
                kind="all_pass",
                claimed_passed=None,
                claimed_total=int(m.group(1)) if m.group(1) else None,
                raw_text=m.group(0),
                span=m.span(),
            )
        )

    return candidates


def _encloses(outer: Claim, inner: Claim) -> bool:
    if outer.span[0] > inner.span[0] or outer.span[1] < inner.span[1]:
        return False
    if outer.span != inner.span:
        return True
    return _KIND_SPECIFICITY[outer.kind] > _KIND_SPECIFICITY[inner.kind]


def _drop_enclosed(candidates: list[Claim]) -> list[Claim]:
    """Discards any candidate wholly contained inside another.

    "22/22 passed" matches both _N_OF_M_RE (span 0-12) and, on the
    substring "22 passed", _N_PASSED_RE (span 3-12). They describe the same
    phrase, not two claims, and the enclosing match is the one that read it
    correctly - confirmed directly: keeping the contained match discarded
    the denominator entirely, so a real 22-passed-1-failed run verified
    "22/22 passed" as true, while the identical lie written "22/22 tests
    pass" was correctly blocked.
    """
    return [
        candidate
        for candidate in candidates
        if not any(_encloses(other, candidate) for other in candidates if other is not candidate)
    ]


def extract_claims(message: str) -> list[Claim]:
    """Returns at most one Claim: the last one found in the message, by
    starting position, regardless of kind. compare.py checks every returned
    claim against a single pytest run, so two claims with different kinds
    or numbers can't both be true at once - confirmed directly, "14 passed.
    Fixed the bug, now 15/15 tests pass!" is a stale count restated and
    corrected using different phrasing (n_passed then n_of_m), not a real
    contradiction, and returning both flagged an honest correction as a
    mismatch. Keeping only the last claim in the text is the same
    last-occurrence-wins principle as before, just applied globally instead
    of per kind. This does not resolve genuine tense/discourse ambiguity
    (a later sentence describing an earlier state is still possible); see
    the README.

    The three stages run in this order, and the order is load-bearing:
    enclosed matches are dropped BEFORE the negation filter. "not 22/22
    passed" negates the n_of_m match at offset 4, but the contained
    "22 passed" starts at offset 7, where the lookback window is "not 22/"
    and reads as un-negated. Filtering negation first leaves that contained
    match alive and registers a claim from a message that explicitly denies
    one. Do not reorder these.
    """
    candidates = _drop_enclosed(_collect_candidates(message))
    candidates = [c for c in candidates if not _is_negated(message, c.span[0])]

    if not candidates:
        return []

    return [max(candidates, key=lambda c: c.span[0])]
