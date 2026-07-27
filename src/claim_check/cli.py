"""Standalone CLI: `claim-check verify-tests <path-or-message>`."""

import argparse
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

from .claims import extract_claims
from .compare import compare_claims
from .runner import result_from_captured_output, run_pytest


def verify_tests(
    path_or_message: str,
    cwd: Path = Path("."),
    pytest_args: Sequence[str] = (),
    pytest_output_file: Optional[Path] = None,
) -> int:
    """Returns a process exit code: 0 for match/no_claim/runner_error
    (runner_error fails open - see compare.py), 1 for mismatch.
    """
    if os.path.isfile(path_or_message):
        message = Path(path_or_message).read_text(encoding="utf-8")
    else:
        message = path_or_message

    claims = extract_claims(message)
    if not claims:
        print("claim-check: no test-count claim found; nothing to verify.")
        return 0

    if pytest_output_file is not None:
        captured = Path(pytest_output_file).read_text(encoding="utf-8")
        run_result = result_from_captured_output(0, captured)
    else:
        run_result = run_pytest(cwd, pytest_args)

    verdict = compare_claims(claims, run_result.counts)

    if verdict.status == "match":
        print(f"claim-check: OK - {verdict.message}")
        return 0
    if verdict.status == "runner_error":
        print(f"claim-check: WARNING - could not verify ({verdict.message}); allowing commit")
        return 0
    print(f"claim-check: MISMATCH - {verdict.message}")
    return 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="claim-check")
    subparsers = parser.add_subparsers(dest="command", required=True)

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
        "pytest_args", nargs=argparse.REMAINDER, help="Extra arguments passed through to pytest"
    )

    args = parser.parse_args(argv)

    if args.command == "verify-tests":
        return verify_tests(
            args.path_or_message,
            cwd=Path(args.cwd),
            pytest_args=args.pytest_args,
            pytest_output_file=Path(args.pytest_output) if args.pytest_output else None,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
