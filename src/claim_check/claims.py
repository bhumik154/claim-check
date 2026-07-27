"""Regex-based extraction of test-count claims from commit-message text.

Deliberately not NLP: a small, fully enumerable set of phrasings, each with
an explicit guard against the false-positive shapes that actually show up
in real commit messages (decimal numbers, issue references, negation).
"""

import re

from .models import Claim

# (?<![.\d#]) blocks three false-positive shapes in one lookbehind: a digit
# preceded by another digit (a partial match into a larger number), by "."
# (the tail of a decimal like "0.22"), or by "#" (an issue reference like
# "#22"). None of these are test-count claims.
_NOT_A_CLAIM_DIGIT_PREFIX = r"(?<![.\d#])"

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
_NEGATION_BEFORE_RE = re.compile(r"\b(not|n't|never)\s*$", re.IGNORECASE)


def _is_negated(message: str, match_start: int) -> bool:
    window = message[max(0, match_start - 20) : match_start]
    return bool(_NEGATION_BEFORE_RE.search(window))


def extract_claims(message: str) -> list[Claim]:
    """Returns at most one Claim per kind. When the same kind appears more
    than once with conflicting numbers (a stale count restated later in the
    same message, then corrected), the last occurrence in the text wins -
    later statements are the more likely current-state claim. This does not
    resolve genuine tense/discourse ambiguity; see the README.
    """
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
        if _is_negated(message, m.start()):
            continue
        claimed_total = int(m.group(1)) if m.group(1) else None
        candidates.append(
            Claim(
                kind="all_pass",
                claimed_passed=None,
                claimed_total=claimed_total,
                raw_text=m.group(0),
                span=m.span(),
            )
        )

    last_by_kind: dict[str, Claim] = {}
    for claim in candidates:
        existing = last_by_kind.get(claim.kind)
        if existing is None or claim.span[0] > existing.span[0]:
            last_by_kind[claim.kind] = claim

    return sorted(last_by_kind.values(), key=lambda c: c.span[0])
