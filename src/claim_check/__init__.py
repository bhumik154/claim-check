"""Verify test-count claims against what the test suite actually did.

pytest, vitest and jest are supported. `PytestCounts` predates the other two
and keeps its name because it is part of the public API; `TestCounts` is the
same type under a name that has aged better, and is the one to reach for in
new code. Its pytest-specific fields (xfailed, xpassed, deselected) are
simply zero for runners with no such concept.
"""

from .claims import extract_claims
from .compare import compare_claims, evaluate
from .js_parser import parse_js_summary
from .models import Claim, PytestCounts, RunResult, Verdict
from .pytest_parser import parse_summary_line, strip_ansi
from .runner import result_from_captured_output, run_pytest
from .runners import TestCounts, parse_test_output
from .shell_parser import extract_commit_message

__version__ = "0.4.0"

__all__ = [
    "__version__",
    "Claim",
    "PytestCounts",
    "TestCounts",
    "RunResult",
    "Verdict",
    "extract_claims",
    "extract_commit_message",
    "parse_summary_line",
    "parse_js_summary",
    "parse_test_output",
    "strip_ansi",
    "run_pytest",
    "result_from_captured_output",
    "compare_claims",
    "evaluate",
]
