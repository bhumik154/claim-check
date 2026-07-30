"""Claude Code PostToolUse hook: records real test runs as evidence.

Observation, not enforcement. This hook **emits nothing and blocks nothing**.
When the agent runs pytest through the Bash tool, its output arrives here for
free, already produced - so the counts can be captured without paying for a
second suite run.

That is what makes checking a claim at the end of a turn affordable at all.
The alternative, re-running pytest on every turn to verify whatever was said,
costs a full suite run per turn and would be removed within a day.

Exits 0 unconditionally and writes nothing to stdout: a PostToolUse hook runs
after the tool has already executed, so there is nothing to allow or deny,
and stdout on exit 0 is parsed as structured hook output.
"""

import sys
from typing import Optional, Sequence

from .._debug import dump_payload
from .._payload import bash_command, resolve_cwd, session_id, tool_stdout
from ..evidence import record_run

import json


def _run(stdin_text: Optional[str]) -> int:
    if stdin_text is None and sys.stdin.isatty():
        # Never reached under Claude Code, which always pipes the payload in;
        # this only stops a developer running the command bare in a terminal
        # from hanging forever on a read that will never return.
        return 0

    raw = stdin_text if stdin_text is not None else sys.stdin.read()
    dump_payload("PostToolUse", raw)

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0

    command = bash_command(payload)
    if command is None:
        return 0

    stdout = tool_stdout(payload)
    if not stdout:
        return 0

    # record_run itself decides whether this was a test run: it returns None
    # when the output holds no pytest summary line. Deciding from the output
    # rather than by pattern-matching the command string means a wrapper
    # ("make test", "poetry run pytest") is recognised on its merits.
    record_run(resolve_cwd(payload), session_id(payload), command, stdout)
    return 0


def main(stdin_text: Optional[str] = None, argv: Optional[Sequence[str]] = None) -> int:
    try:
        return _run(stdin_text)
    except Exception as exc:  # noqa: BLE001 - deliberate catch-all backstop
        # Recording evidence is a convenience. Nothing it can hit is worth
        # surfacing an error into the user's session, let alone a nonzero
        # exit from a hook.
        print(f"claim-check: WARNING - could not record test evidence ({exc!r})", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
