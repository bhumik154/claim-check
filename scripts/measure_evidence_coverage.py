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

Classification is delegated to `claim_check.session_stats.summarize_session`,
the same code the live Stop hook uses, so this script cannot drift from it.

One caveat on the numbers it prints: "same-session evidence" is an upper
bound. The real hook additionally requires the source tree to be unchanged
since the run, and that is not reconstructible from a transcript, so the true
figure is at or below the backed figure below.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from claim_check import session_stats  # noqa: E402
from claim_check.session_stats import NARROWED_ONLY, NO_RUN, STALE  # noqa: E402

TTL_S = 900.0


def main(argv):
    root = Path(argv[1]) if len(argv) > 1 else Path.home() / ".claude" / "projects"
    if not root.is_dir():
        print(f"no transcripts directory at {root}")
        return 1

    sessions = backed = 0
    causes = {NO_RUN: 0, NARROWED_ONLY: 0, STALE: 0}

    for transcript in sorted(root.rglob("*.jsonl")):
        if "subagents" in transcript.parts:
            continue
        stats = session_stats.summarize_session(transcript, ttl_s=TTL_S, budget_s=None)
        if stats is None:
            continue
        sessions += 1
        backed += stats.backed
        for key, count in stats.causes.items():
            causes[key] = causes.get(key, 0) + count

    claims = backed + sum(causes.values())
    print("=" * 68)
    print("Would an end-of-turn claim check have anything to check against?")
    print("=" * 68)
    print(f"  sessions scanned          : {sessions}")
    print(f"  turns stating a claim     : {claims}")
    print()
    print(f"  backed by a whole-suite run within the {TTL_S/60:.0f}min TTL "
          f": {backed}/{claims}")
    print(f"     no test run at all     : {causes[NO_RUN]}")
    print(f"     only narrowed runs     : {causes[NARROWED_ONLY]}")
    print(f"     run too old to use     : {causes[STALE]}")
    print()
    print("  The real check also requires the tree to be unchanged since")
    print("  that run, which a transcript cannot show, so true coverage is")
    print("  at or below the backed figure.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
