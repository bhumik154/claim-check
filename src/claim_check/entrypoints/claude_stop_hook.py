"""Claude Code Stop hook: checks a claim made in conversation, not in a commit.

The PreToolUse hook catches a false test count in a commit message. This
catches the far commoner case: an agent asserting "all tests pass" in a
summary, having run nothing.

**It declares and never blocks.** A false positive here is much more
intrusive than one on `git commit`, where the user asked for an action and
expects a gate. Blocking stays deferred until this has produced a measured
false-positive rate.

**It never runs pytest.** It compares against evidence the PostToolUse hook
recorded from runs the agent already made. Re-running a suite at the end of
every turn is what would get this uninstalled inside a day.

The gate is narrow on purpose. A finding requires all of:
  - stop_hook_active false
  - a claim in the final assistant message, once quotations are stripped
  - fresh, same-session evidence
  - from a run that was not scope-narrowing
  - that reported no errors
  - and that contradicts the claim
Anything else is silence. Measured across 43 real sessions, claims appear on
1.8% of turns and about half have usable evidence, so the quiet path is
overwhelmingly the common one.
"""

import json
import os
import sys
from typing import Optional, Sequence

from .._debug import dump_payload
from .._payload import resolve_cwd, session_id
from ..compare import compare_claims
from ..conversation import extract_claims
from ..evidence import counts_from_record, load_fresh
from ..transcript import last_assistant_text

# Appended to every finding. The one outcome this must never encourage is an
# agent deleting an assertion so an earlier sentence becomes true, so the
# instruction is a fixed constant rather than generated text, and a test
# asserts it appears in every finding.
CORRECTION_INSTRUCTION = (
    "Correct the statement to match the observed result. "
    "Do not modify tests or source code to make the earlier statement true."
)


def _note(message: str) -> str:
    return json.dumps({"systemMessage": message})


def _run(stdin_text: Optional[str]) -> int:
    if stdin_text is None and sys.stdin.isatty():
        return 0

    raw = stdin_text if stdin_text is not None else sys.stdin.read()
    dump_payload("Stop", raw)

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0

    # Recursion guard, checked before anything else. Claude Code sets this
    # while a Stop hook is already in flight; ignoring it risks a loop.
    if payload.get("stop_hook_active") is True:
        return 0

    transcript_path = payload.get("transcript_path")
    if not isinstance(transcript_path, str):
        return 0

    text = last_assistant_text(transcript_path)
    if not text:
        return 0

    # Cheap prefilter before any parsing. Every claim shape contains "pass",
    # so one case-insensitive scan drops the ~98% of turns that assert
    # nothing about tests.
    if "pass" not in text.lower():
        return 0

    claims = extract_claims(text)
    if not claims:
        return 0

    claim = claims[0]
    record = load_fresh(resolve_cwd(payload), session_id(payload))

    if record is None or record.get("scoped") is True:
        # No usable evidence. Unknown is never reported as wrong; the note
        # exists only for someone who has opted into seeing it.
        if os.environ.get("CLAIM_CHECK_VERBOSE"):
            print(_note(
                f'claim-check: could not verify "{claim.raw_text}". No whole-suite '
                "test run was recorded in this session against the current state "
                "of the tree."
            ))
        return 0

    counts = counts_from_record(record)
    if counts is None or counts.errors:
        # An error means some test never ran, so the true denominator is
        # unknown. Blocking a commit on that is defensible; nagging about it
        # in conversation is not.
        return 0

    verdict = compare_claims([claim], counts)
    if verdict.status != "mismatch":
        return 0

    print(_note(
        f"claim-check: {verdict.message}, according to the last full test run "
        f"in this session ({counts.raw_summary_line.strip()}). "
        f"{CORRECTION_INSTRUCTION}"
    ))
    return 0


def main(stdin_text: Optional[str] = None, argv: Optional[Sequence[str]] = None) -> int:
    try:
        return _run(stdin_text)
    except Exception as exc:  # noqa: BLE001 - deliberate catch-all backstop
        # Nothing this hook can hit internally is evidence a claim is wrong.
        print(f"claim-check: WARNING - end-of-turn check failed ({exc!r})", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
