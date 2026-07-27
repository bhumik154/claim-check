"""Claude Code PreToolUse hook entry point.

Reads the hook JSON from stdin, checks whether the tool call is a
`git commit` invocation, extracts the commit message from the raw shell
command string, and denies the tool call (via the PreToolUse JSON response
schema) if a test-count claim in it doesn't match the actual pytest
results.

Fails open at every uncertain step: a non-Bash tool call, a shell-parse
failure, an empty claim list, or a pytest crash are all treated as "allow",
never as grounds to block. A parse failure is not evidence a claim is
wrong, it's just evidence we couldn't check.
"""

import json
import sys
from typing import Optional

from ..claims import extract_claims
from ..compare import compare_claims
from ..runner import run_pytest
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


def main(stdin_text: Optional[str] = None) -> int:
    raw = stdin_text if stdin_text is not None else sys.stdin.read()

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return 0

    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return 0

    command = payload.get("tool_input", {}).get("command", "")
    message = extract_commit_message(command)
    if message is None:
        return 0

    claims = extract_claims(message)
    if not claims:
        return 0

    cwd = payload.get("cwd", ".")
    run_result = run_pytest(cwd)
    verdict = compare_claims(claims, run_result.counts)

    if verdict.status == "mismatch":
        print(_deny_json(verdict.message))
        return 0

    # match, no_claim, or runner_error (fails open per the resolved crash
    # policy) all allow silently: no stdout means Claude Code proceeds
    # normally.
    return 0


if __name__ == "__main__":
    sys.exit(main())
