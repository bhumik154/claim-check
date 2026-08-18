"""Session-level statistics about test-count claims.

Answers one question for a single session: of the claims made, how many had a
whole-suite test run behind them, and for the rest, why not.

Nothing is stored. The numbers are recomputed from the transcript on demand,
because the alternative is a counter that can go stale and then lies
confidently, which is the failure this whole project exists to prevent.

The reported quantity is deliberately "had a whole-suite run behind them" and
never "verified". These statistics are reconstructed from the transcript,
while the live check additionally requires the source fingerprint to be
unchanged, and a transcript cannot show that. Reconstruction can therefore
count a claim as backed that the live check would have rejected as stale.
Naming the weaker thing that is actually measured keeps the sentence true.
"""

from dataclasses import dataclass
from typing import Mapping

NO_RUN = "no_run"
NARROWED_ONLY = "narrowed_only"
STALE = "stale"

# Fixed order, so equal counts always render the same way. Without this the
# message would depend on dict insertion order, which depends on the order
# turns happen to appear in the transcript.
_CAUSE_PHRASES = (
    (NO_RUN, "had no test run at all"),
    (NARROWED_ONLY, "had only narrowed runs"),
    (STALE, "had a run too old to use"),
)


@dataclass(frozen=True)
class SessionStats:
    backed: int
    unbacked: int
    causes: Mapping[str, int]

    def render(self) -> str:
        """The parenthetical appended to an unverified-claim warning.

        Empty when no claims were seen, so a caller can append it
        unconditionally.
        """
        total = self.backed + self.unbacked
        if total <= 0:
            return ""

        body = (
            f"this session: {self.backed} of {total} claims had a "
            "whole-suite run behind them"
        )

        dominant = self._dominant_cause()
        if dominant is None:
            return f"({body})"
        count, phrase = dominant
        return f"({body}; {count} {phrase})"

    def _dominant_cause(self):
        """The most common known cause as (count, phrase), or None.

        Unknown keys are ignored rather than rendered, so a cause added by a
        later version cannot produce a broken sentence here.
        """
        best = None
        for key, phrase in _CAUSE_PHRASES:
            count = self.causes.get(key, 0)
            if isinstance(count, int) and count > 0:
                if best is None or count > best[0]:
                    best = (count, phrase)
        return best
