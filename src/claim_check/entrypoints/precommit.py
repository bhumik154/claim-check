"""pre-commit framework commit-msg stage entry point.

Mirrors conventional-pre-commit's own contract exactly: the commit-message
file path arrives as a positional argument (git's own commit-msg hook
contract, wrapped by pre-commit), read as UTF-8, plain exit-code contract
(0 = pass, nonzero = abort the commit). Deliberately differs from
conventional-pre-commit on one point: a UnicodeDecodeError fails open here
(returns success with a warning) rather than failing the commit, since a
decode error isn't evidence any test-count claim is wrong - it's an
environment issue, and this project's whole design principle is to never
punish what it can't verify.
"""

import argparse
import sys
from typing import Optional, Sequence

from .._args import positive_timeout
from ..claims import extract_claims
from ..compare import compare_claims
from ..runner import DEFAULT_TIMEOUT_S, run_pytest

RESULT_SUCCESS = 0
RESULT_FAIL = 1


def _read_message(input_path: str) -> Optional[str]:
    try:
        with open(input_path, encoding="utf-8") as f:
            return f.read()
    except (UnicodeDecodeError, OSError):
        # UnicodeDecodeError: corrupted bytes, not evidence a claim is wrong.
        # OSError (covers FileNotFoundError, PermissionError, etc.): git and
        # pre-commit always pass a real COMMIT_EDITMSG path, but a developer
        # manually testing this hook from a terminal with a typo'd or
        # nonexistent path shouldn't get an unhandled traceback for it.
        print("claim-check: commit message file missing or not valid UTF-8; skipping verification")
        return None


def _verify(args) -> int:
    message = _read_message(args.input)
    if message is None:
        return RESULT_SUCCESS

    claims = extract_claims(message)
    if not claims:
        return RESULT_SUCCESS

    run_result = run_pytest(args.cwd, timeout_s=args.timeout, command=args.command)
    verdict = compare_claims(claims, run_result.counts)

    if verdict.status == "mismatch":
        print(f"claim-check: {verdict.message}")
        return RESULT_FAIL

    if verdict.status == "runner_error":
        print(f"claim-check: WARNING - could not verify ({run_result.parse_error}); allowing commit")

    return RESULT_SUCCESS


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="claim-check-precommit")
    parser.add_argument("input", help="A file containing a git commit message")
    parser.add_argument("--cwd", default=".", help="Directory to run pytest in")
    parser.add_argument(
        "--command",
        default=None,
        help=(
            "Override the test-runner command (default: '<python> -m pytest'). "
            "Needed when this hook's own environment isn't the one with the "
            "project's real test dependencies, e.g. --command \"poetry run pytest\"."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=positive_timeout,
        default=DEFAULT_TIMEOUT_S,
        help=f"Kill the test run and fail open after this many seconds (default: {DEFAULT_TIMEOUT_S})",
    )
    args = parser.parse_args(argv)

    try:
        return _verify(args)
    except Exception as exc:  # noqa: BLE001 - deliberate catch-all backstop
        # This runs as a commit-msg hook, where any nonzero exit aborts the
        # commit. No internal defect may ever do that: a crash is not
        # evidence a claim is wrong. Argument parsing stays outside the
        # guard on purpose - see _args.positive_timeout. The message-file
        # read is now inside this guard (via _verify -> _read_message): its
        # own specific (UnicodeDecodeError, OSError) handling covers the
        # normal missing/undecodable case, but anything else it could raise
        # (e.g. ValueError from an embedded null byte in the path) is still
        # caught here rather than escaping as a traceback that aborts the
        # commit.
        print(f"claim-check: WARNING - could not verify (internal error: {exc!r}); allowing commit")
        return RESULT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
