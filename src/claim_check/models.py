"""Data model shared by every entry point: claims extracted from commit
message text, pytest's actual results, and the verdict comparing them."""

from dataclasses import dataclass
from typing import Literal, Optional


@dataclass(frozen=True)
class Claim:
    kind: Literal["n_passed", "n_of_m", "all_pass"]
    """"n_passed": bare "N passed". "n_of_m": "N/M tests" or "N/M passing".
    "all_pass": "all tests pass" or "all N tests pass"."""
    claimed_passed: Optional[int]
    """The claimed pass count. None only for a bare "all tests pass" with no
    number attached."""
    claimed_total: Optional[int]
    """The claimed total/denominator. None unless the claim states one
    (an "N/M" claim, or "all N tests pass")."""
    raw_text: str
    """The exact substring of the message that was matched, for reporting."""
    span: tuple[int, int]
    """Character offsets (start, end) into the original message."""


@dataclass(frozen=True)
class PytestCounts:
    passed: int
    failed: int
    skipped: int
    xfailed: int
    xpassed: int
    errors: int
    warnings: int
    deselected: int
    duration_s: float
    raw_summary_line: str

    @property
    def total(self) -> int:
        """Tests actually collected and run. Excludes errors (pre-test
        collection failures, not tests that ran) and deselected (tests that
        were never asked to run)."""
        return self.passed + self.failed + self.skipped + self.xfailed + self.xpassed


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: str
    stderr: str
    counts: Optional[PytestCounts]
    """None iff no summary line (and no "no tests ran" line) could be found
    at all, meaning pytest itself crashed before finishing."""
    parse_error: Optional[str]
    """Human-readable reason, populated iff counts is None."""
    argv: tuple = ()
    """The arguments actually appended to the runner invocation."""
    cwd: str = ""
    """The directory the runner actually ran in."""
    command: Optional[str] = None
    """The --command override actually used, or None for the default runner.

    These three exist so a caller can tell afterwards what was really run.
    Without them the result withholds information its own caller needs: the
    commit path has to know whether its own invocation was narrowed before it
    can fairly judge a claim about the whole suite."""


@dataclass(frozen=True)
class RunScope:
    """What the run that produced these counts actually covered.

    A claim is about a scope; the tool only sees what the runner collected
    for the invocation it made. "You lied" and "I ran a narrower scope"
    produce byte-identical counts, so scope cannot be recovered from the
    numbers. It has to come from the invocation, which is what this carries.
    """

    narrowed: bool
    """True only when narrowing was positively detected. Not "unknown"."""
    reason: Optional[str] = None
    """What narrowed it, for a message that teaches rather than accuses."""


@dataclass(frozen=True)
class Verdict:
    status: Literal["no_claim", "match", "mismatch", "runner_error", "unverifiable"]
    message: str
    """Human-readable explanation, ready to print or return as a hook denial
    reason."""
    claims: list[Claim]
    counts: Optional[PytestCounts]
