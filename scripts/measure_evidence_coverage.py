"""Measures whether an end-of-turn claim check would have anything to check.

Reads Claude Code transcripts already on disk and answers, for every assistant
turn that stated a test-count claim: was there a real, whole-suite pytest run
earlier in that same session that the claim could have been checked against?

This exists because the alternative was guessing. A Stop hook that verifies
claims is only worth building if usable evidence is normally present; if it
almost never is, the feature is theatre. Rather than build it and find out,
this measures it from sessions that already happened.

Usage:
    python scripts/measure_evidence_coverage.py [transcripts-dir]

Default transcripts-dir is ~/.claude/projects.

Three caveats on the numbers it prints:

  * "same-session evidence" is an upper bound. The real hook additionally
    requires the source tree to be unchanged since the run, and that is not
    reconstructible from a transcript, so the true figure is at or below the
    TTL-constrained number.
  * Scope classification reuses the shipped `evidence.is_scoped`, so it
    inherits that function's deliberate bias: anything ambiguous counts as
    scoped and therefore unusable.
  * Claim detection strips fenced blocks, indented blocks and summary-shaped
    lines first. Without that, quoted pytest output is counted as an
    assertion, which is the single likeliest false positive for a
    conversation-level check.
"""

import datetime
import json
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from claim_check import evidence  # noqa: E402
from claim_check.claims import extract_claims  # noqa: E402
from claim_check.pytest_parser import parse_summary_line  # noqa: E402

TTL_S = 900.0

_FENCE = re.compile(r"```.*?```", re.DOTALL)
_INDENTED = re.compile(r"^(?: {4}|\t).*$", re.MULTILINE)
_SUMMARY_SHAPED = re.compile(r"^.*\b\d+ (?:passed|failed|skipped|error).*\bin \d[\d.]*s.*$", re.MULTILINE)


def strip_quoted_output(text: str) -> str:
    """Removes the shapes that are quoted output rather than an assertion."""
    text = _FENCE.sub("", text)
    text = _INDENTED.sub("", text)
    return _SUMMARY_SHAPED.sub("", text)


def timestamp_of(record: dict):
    raw = record.get("timestamp")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def load(transcript: Path) -> list:
    records = []
    try:
        for line in transcript.open(encoding="utf-8", errors="replace"):
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return records


def command_index(records: list) -> dict:
    """tool_use id -> the Bash command that produced it.

    A result record links back through message.content[].tool_use_id. Note
    that `sourceToolUseID` is null on these records, so it cannot be used.
    """
    index = {}
    for record in records:
        content = (record.get("message") or {}).get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    command = (block.get("input") or {}).get("command")
                    if isinstance(command, str):
                        index[block.get("id")] = command
    return index


def command_for(record: dict, index: dict) -> str:
    content = (record.get("message") or {}).get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                return index.get(block.get("tool_use_id"), "")
    return ""


def assistant_text(record: dict) -> str:
    content = (record.get("message") or {}).get("content")
    if not isinstance(content, list):
        return ""
    return "\n".join(
        block.get("text", "") for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def main(argv):
    root = Path(argv[1]) if len(argv) > 1 else Path.home() / ".claude" / "projects"
    if not root.is_dir():
        print(f"no transcripts directory at {root}")
        return 1

    stats = dict(sessions=0, turns=0, runs=0, unscoped_runs=0,
                 claims_raw=0, claims=0, with_evidence=0,
                 with_unscoped=0, within_ttl=0)

    for transcript in sorted(root.rglob("*.jsonl")):
        if "subagents" in transcript.parts:
            continue
        records = load(transcript)
        if not records:
            continue
        stats["sessions"] += 1
        index = command_index(records)
        have_evidence = have_unscoped = False
        last_unscoped_at = None

        for record in records:
            result = record.get("toolUseResult")
            if isinstance(result, dict) and isinstance(result.get("stdout"), str):
                if parse_summary_line(result["stdout"]) is not None:
                    stats["runs"] += 1
                    have_evidence = True
                    try:
                        argv_tokens = shlex.split(command_for(record, index))
                    except ValueError:
                        argv_tokens = []
                    if argv_tokens and not evidence.is_scoped(argv_tokens):
                        stats["unscoped_runs"] += 1
                        have_unscoped = True
                        last_unscoped_at = timestamp_of(record)

            if record.get("type") != "assistant":
                continue
            text = assistant_text(record)
            if not text.strip():
                continue
            stats["turns"] += 1

            if extract_claims(text):
                stats["claims_raw"] += 1
            if not extract_claims(strip_quoted_output(text)):
                continue

            stats["claims"] += 1
            if have_evidence:
                stats["with_evidence"] += 1
            if have_unscoped:
                stats["with_unscoped"] += 1
                now = timestamp_of(record)
                if last_unscoped_at and now and (now - last_unscoped_at) <= TTL_S:
                    stats["within_ttl"] += 1

    turns = max(stats["turns"], 1)
    claims = max(stats["claims"], 1)
    quoted_only = stats["claims_raw"] - stats["claims"]

    print("=" * 68)
    print("Would an end-of-turn claim check have anything to check against?")
    print("=" * 68)
    print(f"  sessions scanned          : {stats['sessions']}")
    print(f"  assistant turns with text : {stats['turns']}")
    print(f"  pytest runs observed      : {stats['runs']}  (whole-suite: {stats['unscoped_runs']})")
    print()
    print(f"  turns stating a claim     : {stats['claims']}  ({stats['claims']/turns*100:.1f}% of turns)")
    print(f"  quoted output miscounted  : {quoted_only}"
          f"  ({quoted_only/max(stats['claims_raw'],1)*100:.0f}% of raw detections)")
    print("    as a claim, before stripping")
    print()
    print(f"  claims w/ any evidence    : {stats['with_evidence']}/{stats['claims']}"
          f"  ({stats['with_evidence']/claims*100:.0f}%)")
    print(f"  claims w/ whole-suite     : {stats['with_unscoped']}/{stats['claims']}"
          f"  ({stats['with_unscoped']/claims*100:.0f}%)")
    print(f"  ...within the {TTL_S/60:.0f}min TTL   : {stats['within_ttl']}/{stats['claims']}"
          f"  ({stats['within_ttl']/claims*100:.0f}%)   <- upper bound on real coverage")
    print()
    print("  The real check also requires the tree to be unchanged since that")
    print("  run, which a transcript cannot show, so true coverage is at or")
    print("  below the last figure.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
