"""Claude Code PreToolUse hook entry point.

Reads the hook JSON from stdin, checks whether the tool call is a
`git commit` invocation, extracts the commit message from the raw shell
command string, and denies the tool call (via the PreToolUse JSON response
schema) if a test-count claim in it doesn't match the actual pytest
results.

Fails open at every uncertain step: a non-Bash tool call, a shell-parse
failure, an empty claim list, or a pytest crash are all treated as "allow",
never as grounds to block. A parse failure is not evidence a claim is
wrong, it's just evidence we couldn't check. A runner_error still prints a
warning (to stderr, not stdout: Claude Code parses stdout as JSON on exit
0, so a plain-text warning belongs on stderr, not mixed into that).
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from .._args import positive_timeout
from ..claims import extract_claims
from ..compare import compare_claims
from ..runner import DEFAULT_TIMEOUT_S, run_pytest
from ..shell_parser import extract_commit_message


def _deny_json(reason: str) -> str:
    return json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
    )


def _parse_args(argv: Optional[Sequence[str]]):
    parser = argparse.ArgumentParser(prog="claim-check-claude-hook")
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
    return parser.parse_args(argv if argv is not None else sys.argv[1:])


def _resolve_cwd(payload: dict) -> str:
    """payload.get("cwd", ".") returns None when the key is present and
    explicitly null, which reached subprocess as the literal directory
    "None"."""
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and Path(cwd).is_dir():
        return cwd
    return "."


def _run(stdin_text: Optional[str], args) -> int:
    if stdin_text is None and sys.stdin.isatty():
        # Claude Code always pipes the hook JSON in (confirmed: a piped
        # stdin reports isatty() == False), so this never fires in real
        # use. It only catches a developer running the command bare in an
        # interactive terminal, which would otherwise hang indefinitely
        # waiting for input that's never coming.
        return 0

    raw = stdin_text if stdin_text is not None else sys.stdin.read()

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return 0

    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return 0

    # Every field below is type-guarded rather than trusted. Claude Code
    # sends a well-formed payload today, but an unhandled AttributeError or
    # TypeError here contradicts this module's whole contract.
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0

    command = tool_input.get("command")
    if not isinstance(command, str):
        return 0

    message = extract_commit_message(command)
    if message is None:
        return 0

    claims = extract_claims(message)
    if not claims:
        return 0

    run_result = run_pytest(_resolve_cwd(payload), timeout_s=args.timeout, command=args.command)
    verdict = compare_claims(claims, run_result.counts)

    if verdict.status == "mismatch":
        print(_deny_json(verdict.message))
        return 0

    if verdict.status == "runner_error":
        print(f"claim-check: WARNING - could not verify ({run_result.parse_error}); allowing commit", file=sys.stderr)

    # match, no_claim, or runner_error (fails open per the resolved crash
    # policy) all allow: no stdout JSON means Claude Code proceeds normally.
    return 0


def main(stdin_text: Optional[str] = None, argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        return _run(stdin_text, args)
    except Exception as exc:  # noqa: BLE001 - deliberate catch-all backstop
        # Nothing this hook can hit internally is evidence a claim is wrong.
        # Argument parsing is deliberately left outside this guard: a
        # mistyped flag is a configuration error, and a hook that silently
        # ignores its own misconfiguration verifies nothing forever.
        print(
            f"claim-check: WARNING - could not verify (internal error: {exc!r}); allowing commit",
            file=sys.stderr,
        )
        return 0


if __name__ == "__main__":
    sys.exit(main())
