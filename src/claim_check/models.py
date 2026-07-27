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


@dataclass(frozen=True)
class Verdict:
    status: Literal["no_claim", "match", "mismatch", "runner_error"]
    message: str
    """Human-readable explanation, ready to print or return as a hook denial
    reason."""
    claims: list[Claim]
    counts: Optional[PytestCounts]
