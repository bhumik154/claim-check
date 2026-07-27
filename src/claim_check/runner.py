"""Runs pytest and turns its output into a RunResult, or lets a caller
supply already-captured output directly (so CI can reuse a test run it
already did, instead of paying for a second full suite run just to check
a commit message)."""

import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence, Union

from .models import RunResult
from .pytest_parser import parse_summary_line


def run_pytest(cwd: Union[str, Path], pytest_args: Sequence[str] = (), timeout_s: Optional[float] = None) -> RunResult:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", *pytest_args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        return RunResult(
            returncode=-1,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
            counts=None,
            parse_error="pytest timed out before finishing",
        )

    return result_from_captured_output(proc.returncode, proc.stdout, proc.stderr)


def result_from_captured_output(returncode: int, stdout: str, stderr: str = "") -> RunResult:
    counts = parse_summary_line(stdout)
    parse_error = None if counts is not None else "no pytest summary line found in the captured output"
    return RunResult(returncode=returncode, stdout=stdout, stderr=stderr, counts=counts, parse_error=parse_error)
