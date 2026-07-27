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

from ..claims import extract_claims
from ..compare import compare_claims
from ..runner import run_pytest

RESULT_SUCCESS = 0
RESULT_FAIL = 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="claim-check-precommit")
    parser.add_argument("input", help="A file containing a git commit message")
    parser.add_argument("--cwd", default=".", help="Directory to run pytest in")
    args = parser.parse_args(argv)

    try:
        with open(args.input, encoding="utf-8") as f:
            message = f.read()
    except UnicodeDecodeError:
        print("claim-check: commit message file is not valid UTF-8; skipping verification")
        return RESULT_SUCCESS

    claims = extract_claims(message)
    if not claims:
        return RESULT_SUCCESS

    run_result = run_pytest(args.cwd)
    verdict = compare_claims(claims, run_result.counts)

    if verdict.status == "mismatch":
        print(f"claim-check: {verdict.message}")
        return RESULT_FAIL

    if verdict.status == "runner_error":
        print(f"claim-check: WARNING - {verdict.message}; allowing commit")

    return RESULT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
