"""Verify test-count claims in commit messages against real pytest output."""

from .claims import extract_claims
from .compare import compare_claims, evaluate
from .models import Claim, PytestCounts, RunResult, Verdict
from .pytest_parser import parse_summary_line, strip_ansi
from .runner import result_from_captured_output, run_pytest
from .shell_parser import extract_commit_message

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "Claim",
    "PytestCounts",
    "RunResult",
    "Verdict",
    "extract_claims",
    "extract_commit_message",
    "parse_summary_line",
    "strip_ansi",
    "run_pytest",
    "result_from_captured_output",
    "compare_claims",
    "evaluate",
]
