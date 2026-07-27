"""Standalone CLI: `claim-check verify-tests <path-or-message>`."""

import argparse
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

from .claims import extract_claims
from .compare import compare_claims
from .runner import DEFAULT_TIMEOUT_S, result_from_captured_output, run_pytest


def verify_tests(
    path_or_message: str,
    cwd: Path = Path("."),
    pytest_args: Sequence[str] = (),
    pytest_output_file: Optional[Path] = None,
    command: Optional[str] = None,
    timeout_s: Optional[float] = DEFAULT_TIMEOUT_S,
) -> int:
    """Returns a process exit code: 0 for match/no_claim/runner_error
    (runner_error fails open - see compare.py), 1 for mismatch.
    """
    if os.path.isfile(path_or_message):
        message = Path(path_or_message).read_text(encoding="utf-8", errors="replace")
    else:
        message = path_or_message

    claims = extract_claims(message)
    if not claims:
        print("claim-check: no test-count claim found; nothing to verify.")
        return 0

    if pytest_output_file is not None:
        try:
            captured = Path(pytest_output_file).read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            print(
                f"claim-check: WARNING - could not verify (pytest output file "
                f"{pytest_output_file} not found); allowing commit"
            )
            return 0
        run_result = result_from_captured_output(0, captured)
    else:
        run_result = run_pytest(cwd, pytest_args, timeout_s=timeout_s, command=command)

    verdict = compare_claims(claims, run_result.counts)

    if verdict.status == "match":
        print(f"claim-check: OK - {verdict.message}")
        return 0
    if verdict.status == "runner_error":
        print(f"claim-check: WARNING - could not verify ({run_result.parse_error}); allowing commit")
        return 0
    print(f"claim-check: MISMATCH - {verdict.message}")
    return 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="claim-check")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    verify_parser = subparsers.add_parser(
        "verify-tests", help="Verify test-count claims in a commit message or a file containing one"
    )
    verify_parser.add_argument(
        "path_or_message", help="A file path containing the message, or the literal message text"
    )
    verify_parser.add_argument("--cwd", default=".", help="Directory to run pytest in")
    verify_parser.add_argument(
        "--pytest-output",
        default=None,
        help="Path to already-captured pytest output; skips running pytest again",
    )
    verify_parser.add_argument(
        "--command",
        default=None,
        help=(
            "Override the test-runner command (default: '<python> -m pytest'). "
            "Use this if your project's dependencies live in a separate environment, "
            "e.g. --command \"poetry run pytest\" or --command \"hatch run test\"."
        ),
    )
    verify_parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        help=f"Kill the test run and fail open after this many seconds (default: {DEFAULT_TIMEOUT_S})",
    )
    # Deliberately not nargs=REMAINDER: that swallows every token after the
    # first positional, including claim-check's own --cwd/--command/
    # --timeout, into pytest_args instead of parsing them - confirmed
    # directly, "verify-tests MSG --cwd X" silently left cwd at its default
    # and passed "--cwd X" through to pytest itself as bogus arguments.
    # parse_known_args lets argparse recognize claim-check's own flags in
    # any position and treats only genuinely-unknown tokens (a pytest path,
    # -k, etc.) as passthrough.
    args, pytest_args = parser.parse_known_args(argv)

    if args.subcommand == "verify-tests":
        return verify_tests(
            args.path_or_message,
            cwd=Path(args.cwd),
            pytest_args=pytest_args,
            pytest_output_file=Path(args.pytest_output) if args.pytest_output else None,
            command=args.command,
            timeout_s=args.timeout,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
