"""Runs pytest and turns its output into a RunResult, or lets a caller
supply already-captured output directly (so CI can reuse a test run it
already did, instead of paying for a second full suite run just to check
a commit message)."""

import shlex
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence, Union

from .models import RunResult
from .pytest_parser import parse_summary_line

# Deliberately generous, not the 15-30s a fast unit suite might suggest: a
# large but perfectly normal integration suite can legitimately take
# minutes, and a timeout that's too short makes this tool silently verify
# nothing (fails open) for every such project. Configurable per invocation;
# this is just the shipped default. Chosen to bound the worst case (a
# hung/broken suite blocking `git commit`, and multiplying across every
# commit touched by an interactive rebase) without punishing normal-sized
# suites by default.
DEFAULT_TIMEOUT_S = 120.0


def _failed_run(reason: str) -> RunResult:
    return RunResult(returncode=-1, stdout="", stderr="", counts=None, parse_error=reason)


def run_pytest(
    cwd: Union[str, Path],
    pytest_args: Sequence[str] = (),
    timeout_s: Optional[float] = DEFAULT_TIMEOUT_S,
    command: Optional[str] = None,
) -> RunResult:
    """command, if given, overrides the default `sys.executable -m pytest`
    invocation - e.g. "poetry run pytest", "hatch run test", or
    "docker-compose exec web pytest" - for projects where the caller's own
    Python environment isn't the one with the project's real test
    dependencies installed (confirmed directly: a claim-check installed
    into its own separate environment, pipx-style, produces a
    ModuleNotFoundError for pytest itself when the project's actual tests
    live in a different poetry/hatch/pipenv-managed environment - silently
    failing open on every commit, exactly as if no verification tool were
    installed at all).

    Every failure path returns a RunResult with counts=None rather than
    raising: this runs inside a commit-msg hook, where an unhandled
    exception means a nonzero exit, which aborts the commit. A
    misconfiguration is not evidence any claim is wrong.
    """
    try:
        base_command = shlex.split(command) if command else [sys.executable, "-m", "pytest"]
    except ValueError as exc:
        # An unbalanced quote in --command. The README tells people to quote
        # this flag, so a typo here is an ordinary user mistake, not a bug.
        return _failed_run(f"could not parse the test command {command!r}: {exc}")

    if not base_command:
        return _failed_run(f"the test command {command!r} is empty")

    try:
        cwd_path = Path(cwd) if cwd is not None else Path(".")
    except TypeError:
        return _failed_run(f"invalid working directory: {cwd!r}")

    try:
        is_directory = cwd_path.is_dir()
        exists = cwd_path.exists()
    except OSError as exc:
        # Path.is_dir() re-raises anything outside its small ignored-errno
        # set - a permission error on a locked share or an ACL-mismatched
        # mount reaches here as PermissionError. An unhandled raise in this
        # module aborts the developer's commit.
        return _failed_run(f"could not inspect the working directory {str(cwd_path)!r}: {exc}")

    if not is_directory:
        problem = "is not a directory" if exists else "not found"
        return _failed_run(f"working directory {problem}: {str(cwd_path)!r}")

    try:
        proc = subprocess.run(
            [*base_command, *pytest_args],
            cwd=str(cwd_path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        return RunResult(
            returncode=-1,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
            counts=None,
            parse_error=f"pytest did not finish within {timeout_s}s and was killed",
        )
    except OSError as exc:
        # Covers FileNotFoundError (a --command wrapper like "poetry" that
        # isn't installed), PermissionError, and the Windows-only
        # NotADirectoryError / WinError 87 shapes. Anything here would
        # otherwise crash this process with an unhandled traceback instead
        # of failing open like every other "couldn't verify" case.
        return _failed_run(f"could not find or run the test command: {base_command[0]!r} ({exc})")

    return result_from_captured_output(proc.returncode, proc.stdout, proc.stderr)


def result_from_captured_output(returncode: int, stdout, stderr: str = "") -> RunResult:
    """stdout is coerced rather than trusted: this is a public, exported
    helper, and CI callers hand it whatever their pipeline captured."""
    if isinstance(stdout, bytes):
        stdout = stdout.decode("utf-8", errors="replace")
    elif not isinstance(stdout, str):
        stdout = "" if stdout is None else str(stdout)

    counts = parse_summary_line(stdout)
    parse_error = None if counts is not None else "no pytest summary line found in the captured output"
    return RunResult(returncode=returncode, stdout=stdout, stderr=stderr, counts=counts, parse_error=parse_error)
